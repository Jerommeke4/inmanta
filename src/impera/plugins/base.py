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

    Contect: bart@impera.io
"""

import inspect
import subprocess
import os

from impera.execute.proxy import DynamicProxy, UnknownException
from impera.ast.statements.call import ExpressionState
from impera.execute.util import Unknown
from impera.execute import NotFoundException


class Context(object):
    """
        An instance of this class is used to pass context to the plugin
    """
    def __init__(self, graph, scope, compiler, function):
        self.graph = graph
        self.scope = scope
        self.compiler = compiler
        self.function = function

    def emit_statement(self, stmt):
        """
            Add a new statement
        """
        self.function.new_statement = stmt

    def get_variable(self, name, scope):
        """
            Get the given variable
        """
        return DynamicProxy.return_value(self.scope.get_variable(name, scope).value)

    def get_data_dir(self):
        """
            Get the path to the data dir (and create if it does not exist yet
        """
        data_dir = os.path.join("data", self.scope.name)

        if not os.path.exists(data_dir):
            os.makedirs(data_dir, exist_ok=True)

        return data_dir


class PluginMeta(type):
    """
        A metaclass that registers subclasses in the parent class.
    """
    def __new__(cls, name, bases, dct):
        subclass = type.__new__(cls, name, bases, dct)
        if hasattr(subclass, "__function_name__"):
            cls.add_function(subclass)
        return subclass

    __functions = {}

    @classmethod
    def add_function(mcs, plugin_class):
        """
            Add a function plugin class
        """
        name = plugin_class.__function_name__
        ns_parts = str(plugin_class.__module__).split(".")
        ns_parts.append(name)
        name = "::".join(ns_parts)

        mcs.__functions[name] = plugin_class

    @classmethod
    def get_functions(cls):
        """
            Get all functions that are registered
        """
        return cls.__functions


class Plugin(object, metaclass=PluginMeta):
    """
        This class models a plugin that can be called from the language.
    """
    def __init__(self, compiler, graph, scope):
        self._graph = graph
        self._scope = scope
        self._compiler = compiler

        self._context = -1
        self._return = None

        if hasattr(self.__class__, "__function__"):
            self.arguments = self._load_signature(self.__class__.__function__)
        else:
            self.arguments = []

        self.new_statement = None

    def _load_signature(self, function):
        """
            Load the signature from the given python function
        """
        arg_spec = inspect.getfullargspec(function)
        if arg_spec.defaults is not None:
            default_start = len(arg_spec.args) - len(arg_spec.defaults)
        else:
            default_start = None

        arguments = []
        for i in range(len(arg_spec.args)):
            arg = arg_spec.args[i]

            if arg not in arg_spec.annotations:
                raise Exception("All arguments of plugin '%s' should be annotated" % function.__name__)

            spec_type = arg_spec.annotations[arg]
            if spec_type == Context:
                self._context = i
            else:
                if default_start is not None and default_start <= i:
                    default_value = arg_spec.defaults[default_start - i]

                    arguments.append((arg, spec_type, default_value))
                else:
                    arguments.append((arg, spec_type))

        if "return" in arg_spec.annotations:
            self._return = arg_spec.annotations["return"]

        return arguments

    def add_argument(self, arg_type, arg_type_name, arg_name, optional=False):
        """
            Add an argument at the next position, of given type.
        """
        self.arguments.append((arg_type, arg_type_name, arg_name, optional))

    def get_signature(self):
        """
            Generate the signature of this plugin
        """
        arg_list = []
        for arg in self.arguments:
            if arg[3]:
                arg_list.append("[%s %s]" % (arg[1], arg[2]))
            else:
                arg_list.append("%s %s" % (arg[1], arg[2]))

        args = ", ".join(arg_list)

        return "%s(%s)" % (self.__class__.__function_name__, args)

    def _is_instance(self, value, arg_type):
        """
            Check if value is of arg_type
        """
        if arg_type == "any":
            return True

        elif arg_type == "list":
            return isinstance(value, list)

        elif arg_type == "expression":
            return isinstance(value, ExpressionState)

        else:
            parts = arg_type.split("::")

            module = parts[0:-1]
            cls_name = parts[-1]

            if len(module) == 0 and cls_name in ("string", "bool", "number"):
                module = ["__types__"]

            try:
                var = self._scope.get_variable(cls_name, module)
            except NotFoundException:
                raise NotFoundException("Unable to find type %s" % arg_type)

            if hasattr(value, "_get_instance"):
                value = value._get_instance()

            return var.value.validate(value)

        return False

    def check_args(self, args):
        """
            Check if the arguments of the call match the function signature
        """
        max_arg = len(self.arguments)
        required = len([x for x in self.arguments if len(x) == 3])

        if len(args) < required or len(args) > max_arg:
            raise Exception("Incorrect number of arguments for %s. Expected at least %d, got %d" %
                            (self.get_signature(), required, len(args)))

        for i in range(len(args)):
            if self.arguments[i][0] is not None and not self._is_instance(args[i], self.arguments[i][1]):
                raise Exception(("Invalid type for argument %d of '%s', it should be " +
                                "%s and %s given.") % (i + 1, self.__class__.__function_name__,
                                self.arguments[i][1], args[i].__class__.__name__))

    def emit_statement(self):
        """
            This method is called to determine if the plugin call pushes a new
            statement
        """
        return self.new_statement

    def get_variable(self, name, scope):
        """
            Get the given variable
        """
        return DynamicProxy.return_value(self._scope.get_variable(name, scope).value)

    def check_requirements(self):
        """
            Check if the plug-in has all it requires
        """
        if "bin" in self.opts and self.opts["bin"] is not None:
            for _bin in self.opts["bin"]:
                p = subprocess.Popen(["bash", "-c", "type -p %s" % _bin], stdout=subprocess.PIPE)
                result = p.communicate()

                if len(result[0]) == 0:
                    print("%s requires %s to be available in $PATH" % (self.__function_name__, _bin))

    def __call__(self, *args):
        """
            The function call itself
        """
        try:
            self.check_requirements()
            new_args = []
            for arg in args:
                new_args.append(DynamicProxy.return_value(arg))

            if self._context >= 0:
                context = Context(self._graph, self._scope, self._compiler, self)
                new_args.insert(self._context, context)

            value = self.call(*new_args)

            if self._return is not None and not isinstance(value, Unknown):
                valid = False
                exception = None

                try:
                    valid = (value is None or self._is_instance(value, self._return))
                except Exception as exp:
                    exception = exp

                if not valid:
                    msg = ""
                    if exception is not None:
                        msg = "\n\tException details: " + str(exception)

                    raise Exception("Plugin %s should return value of type %s ('%s' was returned) %s" %
                                    (self.__class__.__function_name__, self._return, value, msg))

            return DynamicProxy.return_value(value)
        except UnknownException as e:
            # just pass it along
            return e.unknown


def plugin(function=None, commands=None):
    """
        Python 3 decorator to register functions with impera
    """
    def curry_name(name=None, commands=None):
        """
            Function to curry the name of the function
        """
        def call(fnc):
            """
                Create class to register the function and return the function itself
            """
            def wrapper(self, *args):
                """
                    Python will bind the function as method into the class
                """
                return fnc(*args)

            nonlocal name, commands

            if name is None:
                name = fnc.__name__

            dictionary = {}
            dictionary["__module__"] = fnc.__module__
            dictionary["__function_name__"] = name
            dictionary["opts"] = {"bin": commands}
            dictionary["call"] = wrapper
            dictionary["__function__"] = fnc

            bases = (Plugin,)
            PluginMeta.__new__(PluginMeta, name, bases, dictionary)

            return fnc

        return call

    if function is None and commands is not None:
        return curry_name(commands=commands)

    if isinstance(function, str):
        return curry_name(function)

    elif function is not None:
        fnc = curry_name()
        return fnc(function)
