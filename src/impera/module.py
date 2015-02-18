"""
    Copyright 2015 Impera

    Licensed under the Apache License, Version 2.0 (the "License");
    you may not use this file except in compliance with the License.
    You may obtain a copy of the License at

        http://www.apache.org/licenses/LICENSE-2.0

    Unless required by applicable law or agreed to in writing, software
    distributed under the License is distributed on an "AS IS" BASIS,
    WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
    See the License for the specific language governing permissions and
    limitations under the License.

    Contact: bart@impera.io
"""

from distutils.version import StrictVersion, Version
import glob
import imp
import logging
import os
from os.path import sys
import re
from subprocess import TimeoutExpired
import subprocess
from urllib3 import util, exceptions
import tempfile
import shutil

import yaml
from impera import env
from impera.config import Config
from impera import parser
from impera.execute import scheduler


LOGGER = logging.getLogger(__name__)


class InvalidModuleException(Exception):
    """
        This exception is raised if a module is invalid
    """


class InvalidModuleFileException(Exception):
    """
        This exception is raised if a module file is invalid
    """


class ProjectNotFoundExcpetion(Exception):
    """
        This exception is raised when Impera is unable to find a valid project
    """


class Project(object):
    """
        An Impera project
    """
    PROJECT_FILE = "project.yml"
    _project = None

    def __init__(self, path):
        """
            @param path: The directory where the project is located
        """
        self.project_path = path

        if not os.path.exists(path):
            raise Exception("Unable to find project directory %s" % path)

        project_file = os.path.join(path, Project.PROJECT_FILE)

        if not os.path.exists(project_file):
            raise Exception("Project directory does not contain a project file")

        with open(project_file, "r") as fd:
            self._project_data = yaml.load(fd)

        if "modulepath" not in self._project_data:
            raise Exception("modulepath is required in the project(.yml) file")

        self.modulepath = [os.path.join(path, x) for x in self._project_data["modulepath"].split(os.path.pathsep)]

        self.downloadpath = None
        if "downloadpath" in self._project_data:
            self.downloadpath = os.path.join(path, self._project_data["downloadpath"])

            if not os.path.exists(self.downloadpath):
                os.mkdir(self.downloadpath)

        self.freeze_file = os.path.join(path, "module.version")
        self._freeze_versions = self._load_freeze(self.freeze_file)

        self.virtualenv = env.VirtualEnv(os.path.join(path, "env"))

        self.modules = self.discover(self.modulepath)

    @classmethod
    def get_project_dir(cls, cur_dir):
        """
            Find the project directory where we are working in. Traverse up until we find Project.PROJECT_FILE or reach /
        """
        project_file = os.path.join(cur_dir, Project.PROJECT_FILE)

        if os.path.exists(project_file):
            return cur_dir

        parent_dir = os.path.abspath(os.path.join(cur_dir, os.pardir))
        if parent_dir == cur_dir:
            raise ProjectNotFoundExcpetion("Unable to find an Impera project")

        return cls.get_project_dir(parent_dir)

    @classmethod
    def get(cls):
        """
            Get the instance of the project
        """
        if cls._project is None:
            cls._project = Project(cls.get_project_dir(os.curdir))

        return cls._project

    def _load_freeze(self, freeze_file: str) -> {}:
        """
            Load the versions defined in the freeze file
        """
        if not os.path.exists(freeze_file):
            return {}

        versions = {}
        with open(freeze_file, "r") as fd:
            for line in fd.readlines():
                line = line.strip()
                if line != "" or not line.startswith("#"):
                    parts = line.split(" ")
                    if len(parts) == 2:
                        name, version = parts[0], StrictVersion(parts[1])

                        versions[name] = version
                    else:
                        print("Ignoring line, invalid format: %s" % line)

        return versions

    def load_plugins(self) -> None:
        """
            Load all plug-ins
        """
        for module in self.modules.values():
            module.load_plugins()

    def discover(self, path: str) -> dict:
        """
            Discover configuration modules in the given path

            @param path: A list of paths to search for modules
        """
        module_dirs = {}
        # generate a module list and take precedence into account
        for module_dir in path:
            for sub_dir in glob.glob(os.path.join(module_dir, '*')):
                if Module.is_valid_module(sub_dir):
                    mod_name = os.path.basename(sub_dir)
                    module_dirs[mod_name] = sub_dir

        # create a list of modules
        modules = {}
        for name, path in module_dirs.items():
            mod = Module(self, path)
            modules[name] = mod

        return modules

    def verify(self) -> None:
        # verify module dependencies
        result = True
        for module in self.modules.values():
            result &= module.verify_requires(self.modules)

            if module._meta["name"] in self._freeze_versions:
                mod_version = StrictVersion(str(module._meta["version"]))
                freeze_version = self._freeze_versions[module._meta["name"]]

                if mod_version != freeze_version:
                    raise Exception("The installed version %s of module %s, does not match the version in the module file %s."
                                    % (mod_version, module._meta["name"], freeze_version))

        if not result:
            raise Exception("Not all module dependencies have been met.")

    def use_virtual_env(self) -> None:
        """
            Use the virtual environment
        """
        self.virtualenv.use_virtual_env()

    def requires(self) -> dict:
        """
            Return the requires of this project
        """
        req = {}
        if "requires" in self._project_data and self._project_data["requires"] is not None:
            for name, spec in self._project_data["requires"].items():
                source, version = spec.split(",")
                req[name] = {"source": source.strip(), "version": version.strip()}

        return req

    def sorted_modules(self) -> list:
        """
            Return a list of all modules, sorted on their name
        """
        names = self.modules.keys()
        names = sorted(names)

        mod_list = []
        for name in names:
            mod_list.append(self.modules[name])

        return mod_list

    def collect_requirements(self):
        """
            Collect the list of all requirements of all modules in the project.
        """
        all_reqs = set()

        for mod in self.modules.values():
            all_reqs.update(mod.get_requirements())

        return all_reqs


class Module(object):
    """
        This class models an Impera configuration module
    """
    requires_fields = ["name", "license"]

    def __init__(self, project: Project, path: str, load: bool=True, **kwmeta: dict):
        """
            Create a new configuration module

            :param project: A reference to the project this module belongs to.
            :param path: Where is the module stored
            :param load: Try to load the module. Use false if the module does not exist yet and
                needs to be installed.
            :param kwmeta: Meta-data
        """
        self._project = project
        self._path = path
        self._meta = kwmeta
        self._plugin_namespaces = []

        if load:
            if not Module.is_valid_module(self._path):
                raise InvalidModuleException(("Module %s is not a valid Impera configuration module. Make sure that a " +
                                              "model/_init.cf file exists and a module.yml definition file.") % self._path)

            self.load_module_file()
            self.is_versioned()

    def get_name(self):
        """
            Returns the name of the module (if the meta data is set)
        """
        if "name" in self._meta:
            return self._meta["name"]

        return None

    name = property(get_name)

    def get_version(self):
        """
            Return the version of this module
        """
        if "version" in self._meta:
            return self._meta["version"]

        return None

    version = property(get_version)

    def get_source(self) -> str:
        """
            Get the source url of this module. If git-http-only is true, we try to convert the all urls that are not valid
            http urls.
        """
        source = self._meta["source"]
        if not Config.getboolean("config", "git-http-only", False):
            return source

        new_source = source
        try:
            result = util.parse_url(source)
            if result.scheme != "http" and result.scheme != "https":
                # try to convert it to an anonymous https url
                new_source = source.replace(result.scheme, "http")
        except exceptions.LocationParseError:
            # probably in git@host:repo format
            m = re.search("^(?P<user>[^@]+)@(?P<host>[^:]+):(?P<repo>.+)$", source)
            if m is not None:
                new_source = "http://%(user)s@%(host)s/%(repo)s" % m.groupdict()

        if new_source != source:
            LOGGER.info("Reformated source from %s to %s" % (source, new_source))

        return new_source

    source = property(get_source)

    def requires(self) -> dict:
        """
            Get the requires for this module
        """
        if "requires" not in self._meta or self._meta["requires"] is None:
            return {}

        req = {}
        for require, defs in self._meta["requires"].items():
            source, version = defs.split(",")
            req[require] = {"source": source, "version": version}

        return req

    def verify_requires(self, module_map: dict) -> bool:
        """
            Check if all the required modules for this module have been loaded
        """
        if "requires" not in self._meta or self._meta["requires"] is None:
            return True

        for require, defs in self._meta["requires"].items():
            if require not in module_map:
                print("Module %s requires the %s module that has not been loaded" % (self._path, require))
                return False

            source, version = defs.split(",")

            module = module_map[require]
            if not module.verify_require(source.strip(), version.strip()):
                print("Module %s requires module %s with version %s which is not loaded" %
                      (self._path, require, version.strip()))
                return False

        return True

    def verify_require(self, source_spec: str, version_spec: str) -> bool:
        """
            Verify if this module satisfies the given source and version spec
        """
        # TODO: verify source
        version_spec = version_spec.strip("\"")
        comp, version = version_spec.split(" ")

        other_version = StrictVersion(version)
        try:
            this_version = StrictVersion(str(self._meta["version"]))
        except ValueError:
            raise Exception("Module %s has an invalid version (%s)" % (self._meta["name"], self._meta["version"]))

        if ">" == comp:
            return this_version > other_version

        elif ">=" == comp:
            return this_version >= other_version

        elif "==" == comp:
            return this_version == other_version

        elif "<=" == comp:
            return this_version <= other_version

        elif "<" == comp:
            return this_version < other_version

        else:
            return False

    def is_versioned(self):
        """
            Check if this module is versioned, and if so the version number in the module file should
            have a tag. If the version has + the current revision can be a child otherwise the current
            version should match the tag
        """
        if not os.path.exists(os.path.join(self._path, ".git")):
            LOGGER.warning("Module %s is not version controlled, we recommend you do this as soon as possible."
                           % self._meta["name"])
            return False

        if "version" in self._meta:
            version_str = str(self._meta["version"])
            version = StrictVersion(version_str)
            higher = "a" in version_str or "b" in version_str

            proc = subprocess.Popen(["git", "show-ref", "--head"], cwd=self._path, stdout=subprocess.PIPE)
            refs = {}
            for line in proc.communicate()[0].decode().split("\n"):
                items = line.split(" ")
                if len(items) > 1:
                    ref_id = items[0]
                    ref = items[1]

                    refs[ref] = ref_id

            ref_spec = "refs/tags/%s" % version
            if not higher:
                if ref_spec not in refs:
                    LOGGER.warning(("Version %s defined in module %s is not available as tag. Use a or b followed by a number" +
                                   " to indicate a pre release (i.e. 0.2b1 for dev of 0.2)") % (version, self._meta["name"]))
                    return False

                else:
                    # check that the id of HEAD matches the id of the version tag
                    if refs["HEAD"] != refs[ref_spec]:
                        LOGGER.warning(("Module %s is set to version %s, but current revision (%s) does not match version " +
                                        "tag (%s).") % (self._meta["name"], version, refs["HEAD"], refs[ref_spec]))
                        return False

            else:
                if ref_spec[:-1] in refs:
                    LOGGER.warning(("Module %s defines this is a development version (a or b appended to version) of %s, but " +
                                   "the release version is already available as tag.") % (self._meta["name"], str(version)))
                    return False

        return True

    @classmethod
    def is_valid_module(cls, module_path):
        """
            Checks if this module is a valid configuration module. A module should contain a
            module.yml file.
        """
        if not os.path.isfile(os.path.join(module_path, "module.yml")):
            return False

        return True

    def load_module_file(self):
        """
            Load the imp module definition file
        """
        with open(os.path.join(self._path, "module.yml"), "r") as fd:
            mod_def = yaml.load(fd)

            if mod_def is None or len(mod_def) < len(Module.requires_fields):
                raise InvalidModuleFileException("The module file of %s does not have the required fields: %s" %
                                                 (self._path, ", ".join(Module.requires_fields)))

            for name, value in mod_def.items():
                self._meta[name] = value

        for req_field in Module.requires_fields:
            if req_field not in self._meta:
                raise InvalidModuleFileException("%s is required in module file of module %s" % (req_field, self._path))

        if self._meta["name"] != os.path.basename(self._path):
            LOGGER.warning("The name in the module file (%s) does not match the directory name (%s)"
                           % (self._meta["name"], os.path.basename(self._path)))

    def get_module_files(self):
        """
            Returns the path of all model files in this module, relative to the module root
        """
        files = []
        for model_file in glob.glob(os.path.join(self._path, "model", "*.cf")):
            files.append(model_file)

        return files

    def load_plugins(self):
        """
            Load all plug-ins from a configuration module
        """
        plugin_dir = os.path.join(self._path, "plugins")

        if not os.path.exists(plugin_dir):
            return

        if not os.path.exists(os.path.join(plugin_dir, "__init__.py")):
            raise Exception("The plugin directory %s should be a valid python package with a __init__.py file" % plugin_dir)

        try:
            mod_name = self._meta["name"]
            imp.load_package(mod_name, plugin_dir)

            self._plugin_namespaces.append(mod_name)

            for py_file in glob.glob(os.path.join(plugin_dir, "*.py")):
                if not py_file.endswith("__init__.py"):
                    # name of the python module
                    sub_mod = mod_name + "." + os.path.basename(py_file).split(".")[0]
                    self._plugin_namespaces.append(sub_mod)

                    # load the python file
                    imp.load_source(sub_mod, py_file)

        except ImportError as e:
            print("Unable to load all plug-ins for module %s" % self._meta["name"])
            print("\t" + e.msg)

    def update(self):
        """
            Update the module by doing a git pull
        """
        sys.stdout.write("Updating %s " % self._meta["name"])
        sys.stdout.flush()

        output = self._call(["git", "pull"], self._path, "git pull")

        if output is not None:
            sys.stdout.write("branches ")
            sys.stdout.flush()

        output = self._call(["git", "pull", "--tags"], self._path, "git pull --tags")

        if output is not None:
            sys.stdout.write("tags ")
            sys.stdout.flush()

        print("done")

    def install(self, modulepath):
        """
            Install this module if it has not been installed yet, and install its dependencies.
        """
        if not os.path.exists(self._path):
            # check if source and version are available
            if "source" not in self._meta or "version" not in self._meta:
                raise Exception("Source and version are required to install a configuration module.")

            cmd = ["git", "clone", self.source, self._meta["name"]]
            output = self._call(cmd, modulepath, "git clone")

            if output is None:
                return None

        # reload the module
        module = Module(self._project, self._path)

        return module

    def checkout_version(self, version: Version):
        """
            Checkout the given version
        """
        # versions = self.versions()

        # if version in versions:
        #    print(version)

    def versions(self):
        """
            Provide a list of all versions available in the repository
        """
        output = self._call(["git", "show-ref", "--head"], self._path, "git qshow-ref")
        lines = output.decode().split("\n")

        version_list = []
        for line in lines:
            obj = re.search("^(?P<hash>[a-f0-9]{40}) refs/tags/(?P<version>.+)$", line)

            if obj:
                try:
                    version_list.append(StrictVersion(obj.group("version")))
                except Exception:
                    pass

        return version_list

    def _call(self, cmd: list, path: str, cmd_name: str) -> str:
        proc = subprocess.Popen(cmd, cwd=path, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            output = proc.communicate(timeout=60)
        except TimeoutExpired:
            print("Unable to %(cmd_name)s module %(name)s, %(cmd_name)s timed out" %
                  {"name": self._meta["name"], "cmd_name": cmd_name})

            return None

        if proc.returncode is None or proc.returncode > 0:
            print("")
            print("Unable to %(cmd_name)s %(name)s, %(cmd_name)s provided this output:" %
                  {"name": self._meta["name"], "cmd_name": cmd_name})
            print(output[0])
            print(output[1])

            return None

        return output[0]

    def status(self):
        """
            Run a git status on this module
        """
        cmd = ["git", "status", "--porcelain"]
        output = self._call(cmd, self._path, "git status").decode()

        files = [x.strip() for x in output.split("\n") if x != ""]

        if len(files) > 0:
            print("Module %s (%s)" % (self._meta["name"], self._path))
            for f in files:
                print("\t%s" % f)

            print()

    def push(self):
        """
            Run a git status on this module
        """
        sys.stdout.write("%s (%s) " % (self._meta["name"], self._path))
        sys.stdout.flush()

        cmd = ["git", "push", "--porcelain"]
        output = self._call(cmd, self._path, "git push")
        if output is not None:
            sys.stdout.write("branches ")
            sys.stdout.flush()

        cmd = ["git", "push", "--porcelain", "--tags"]
        output = self._call(cmd, self._path, "git push tags")
        if output is not None:
            sys.stdout.write("tags ")
            sys.stdout.flush()

        print("done")

    def python_install(self):
        """
            Install python requirements with pip in a virtual environment
        """
        self._project.virtualenv.install_from_file(os.path.join(self._path, "requirements.txt"))

    def get_requirements(self):
        """
            Returns an array of requirements of this module
        """
        if not os.path.exists(os.path.join(self._path, "requirements.txt")):
            return set()

        with open(os.path.join(self._path, "requirements.txt"), "r") as fd:
            return set([l.strip() for l in fd.readlines()])


class ModuleTool(object):
    """
        A tool to manage configuration modules
    """
    def __init__(self):
        self._mod_handled_list = set()

    def execute(self, cmd, args):
        """
            Execute the given command
        """
        if hasattr(self, cmd):
            method = getattr(self, cmd)
            method(*args)

        else:
            raise Exception("%s not implemented" % cmd)

    def help(self):
        """
            Show a list of commands
        """
        print("Available commands: list")

    def list(self):
        """
            List all modules in a table
        """
        table = []
        name_length = len("Name") + 5
        version_length = 7
        names = sorted(Project.get().modules.keys())
        for name in names:
            mod = Project.get().modules[name]
            if "version" in mod._meta:
                version = str(mod._meta["version"])
                table.append((name, version))

                if len(name) > name_length:
                    name_length = len(name)

                if len(version) > version_length:
                    version_length = len(version)
            else:
                print("Module %s does not contain a version, invalid module" % name)

        print("+" + "-" * (name_length + version_length + 5) + "+")
        print("| Name%s | Version%s |" % (" " * (name_length - len("Name")), " " * (version_length - len("Version"))))
        print("+" + "-" * (name_length + version_length + 5) + "+")
        for name, version in table:
            print("| %s | %s |" % (name + " " * (name_length - len(name)), version + " " * (version_length - len(version))))

        print("+" + "-" * (name_length + version_length + 5) + "+")

    def update(self):
        """
            Update all modules from their source
        """
        for mod in Project.get().sorted_modules():
            mod.update()

    def _install(self, project, module_path, requires):
        """
            Do a recursive install
        """
        for name, spec in requires.items():
            mod_path = os.path.join(module_path, name)
            if mod_path not in self._mod_handled_list:
                module = Module(project, mod_path, load=False, source=spec["source"], version=spec["version"], name=name)
                new_mod = module.install(module_path)

                new_mod.python_install()
                new_mod.checkout_version(StrictVersion("0.1"))

                self._mod_handled_list.add(mod_path)

                self._install(project, module_path, new_mod.requires())

    def install(self):
        """
            Install all modules the project requires
        """
        project = Project.get()

        if not os.path.exists("project.yml"):
            raise Exception("Project file (project.yml) not found")

        with open("project.yml", "r") as fd:
            project_data = yaml.load(fd)

        if "downloadpath" not in project_data:
            raise Exception("downloadpath is required in the project file to install modules.")

        module_path = project_data["downloadpath"]
        self._install(project, module_path, project.requires())

        install_set = [os.path.realpath(path) for path in self._mod_handled_list]
        not_listed = []
        for mod in project.modules.values():
            if os.path.realpath(mod._path) not in install_set:
                not_listed.append(mod)

        if len(not_listed) > 0:
            print("WARNING: The following modules are loaded by Impera but are not listed in the project file or in " +
                  "the dependencies of other modules:")
            for mod in not_listed:
                print("\t%s (%s)" % (mod._meta["name"], mod._path))

    def status(self):
        """
            Run a git status on all modules and report
        """
        for mod in Project.get().sorted_modules():
            mod.status()

    def push(self):
        """
            Push all modules
        """
        for mod in Project.get().sorted_modules():
            mod.push()

    def freeze(self):
        """
            Freeze the version of all modules
        """
        project = Project.get()
        if os.path.exists(project.freeze_file):
            print("A module.version file already exists, overwrite this file? y/n")
            while True:
                value = sys.stdin.readline().strip()
                if value != "y" and value != "n":
                    print("Please answer with y or n")
                else:
                    if value == "n":
                        return

                    break

        file_content = ""

        for mod in Project.get().sorted_modules():
            version = str(mod._meta["version"])
            if "a" in version or "b" in version:
                print("Cannot include an alpha (a) or beta (b) version in a freeze file because" +
                      ("only exact git tags are allowed. Please create a version for module %s" % mod._meta["name"]) +
                      " which now has version %s" % version)
                return

            file_content += "%s %s\n" % (mod._meta["name"], version)

        with open(project.freeze_file, "w+") as fd:
            fd.write(file_content)

    def verify(self):
        """
            Verify dependencies and frozen module versions
        """
        Project.get().verify()

    def commit(self, version=None):
        """
            Commit all current changes.
        """
        subprocess.call(["git", "commit", "-a"])

    def validate(self):
        """
            Validate the module we are currently in
        """
        valid = True
        module = Module(None, os.path.realpath(os.curdir))
        LOGGER.info("Successfully loaded module %s with version %s" % (module.name, module.version))

        if not module.is_versioned():
            valid = False

        # compile the source files in the module
        model_parser = parser.Parser()
        for model_file in module.get_module_files():
            try:
                model_parser.parse(module.name, model_file)
                LOGGER.info("Successfully parsed %s" % model_file)
            except Exception:
                valid = False
                LOGGER.exception("Unable to parse %s" % model_file)

        # create a test project
        LOGGER.info("Creating a new project to test the module")
        project_dir = tempfile.mkdtemp()
        try:
            lib_dir = os.path.join(project_dir, "libs")
            os.mkdir(lib_dir)

            LOGGER.info("Cloning %s module" % module.name)
            proc = subprocess.Popen(["git", "clone", module._path], cwd=lib_dir)
            proc.wait()

            LOGGER.info("Setting up project")
            with open(os.path.join(project_dir, "project.yml"), "w+") as fd:
                fd.write("""name: test
description: Project to validate module %(name)s
modulepath: libs
downloadpath: libs
requires:
    %(name)s: %(source)s, "==%(version)s"
""" % {"name": module.name, "version": module.version, "source": module._path})

            LOGGER.info("Installing dependencies")
            test_project = Project(project_dir)
            test_project.use_virtual_env()
            self._install(test_project, os.path.join(project_dir, "libs"), test_project.requires())

            LOGGER.info("Compiling empty initial model")
            main_cf = os.path.join(project_dir, "main.cf")
            with open(main_cf, "w+") as fd:
                fd.write("")

            project = Project(project_dir)
            Project._project = project
            project.verify()

            from impera.compiler import main
            compiler = main.Compiler(main_cf)
            statements = compiler.compile()
            sched = scheduler.Scheduler(compiler.graph)
            success = sched.run(compiler, statements)

            if success:
                LOGGER.info("Successfully compiled module and its dependencies.")
            else:
                LOGGER.warning("Unable to compile module and its dependencies.")
                valid = False

        finally:
            # print(project_dir)
            shutil.rmtree(project_dir)

        if not valid:
            sys.exit(1)
