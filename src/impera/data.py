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

from mongoengine import Document
from mongoengine.fields import StringField, ReferenceField, DateTimeField, IntField, MapField, UUIDField, DictField, \
    DynamicField
from impera.resources import Id


class Environment(Document):
    """
        A deployment environment of a project

        :param id A unique, machine generated id
        :param name The name of the deployment environment.
        :param project The project this environment belongs to.
        :param repo_url The repository url that contains the configuration model code for this environment
        :param repo_url The repository branch that contains the configuration model code for this environment
    """
    id = UUIDField(primary_key=True)
    name = StringField(required=True, unique_with=['project'])
    project = ReferenceField("Project", required=True)
    repo_url = StringField()
    repo_branch = StringField()

    def to_dict(self):
        return {"id": str(self.id), "name": self.name, "project": str(self.project.id), "repo_url": self.repo_url,
                "repo_branch": self.repo_branch}


class Project(Document):
    """
        An impera configuration project

        :param id A unique, machine generated id
        :param name The name of the configuration project.
    """
    id = UUIDField(primary_key=True)
    name = StringField(required=True, unique=True)

    def to_dict(self):
        return {"name": self.name, "id": str(self.id)}


SOURCE = ("fact", "plugin", "user")


class Parameter(Document):
    """
        A parameter that can be used in the configuration model

        :param name The name of the parameter
        :param value The value of the parameter
        :param environment The environment this parameter belongs to
        :param source The source of the parameter
        :param resource_id An optional resource id
        :param updated When was the parameter updated last

        :todo Add history
    """
    name = StringField(required=True, unique_with=["environment", "resource_id"])
    value = DynamicField(default="", required=True)
    environment = ReferenceField(Environment, required=True)
    source = StringField(required=True, choices=SOURCE)
    resource_id = StringField(default="")
    updated = DateTimeField()

    def to_dict(self):
        return {"environment": str(self.environment.id),
                "name": self.name,
                "value": self.value,
                "source": self.source,
                "resource_id": self.resource_id,
                "updated": self.updated.isoformat()}


class UnknownParameter(Document):
    """
        A parameter that the compiler indicated that was unknown. This parameter causes the configuration model to be
        incomplete for a specific environment.

        :param name
        :param resource_id
        :param source
        :param environment
        :param version The version id of the configuration model on which this parameter was reported
    """
    name = StringField(required=True, unique_with=["environment", "resource_id", "version"])
    environment = ReferenceField(Environment, required=True)
    source = StringField(required=True, choices=SOURCE)
    resource_id = StringField(default="")
    version = IntField(required=True)


class Node(Document):
    """
        A physical server/node in the infrastructure that reports to the management server.

        :param hostname The hostname of the device.
        :param last_seen When did the server receive data from the node for the last time.
    """
    hostname = StringField(primary_key=True, required=True)
    last_seen = DateTimeField()

    def to_dict(self):
        return {"hostname": self.hostname, "last_seen": self.last_seen.isoformat()}


ROLES = ("server", "agent")


class Agent(Document):
    """
        An impera agent that runs on a device.

        :param environment The environment this resource is defined in
        :param node The node on which this agent is deployed
        :param resources A list of resources that this agent handles
        :param name The name of this agent
        :param role The role of this agent
        :param last_seen When did the server receive data from the node for the last time.
        :param interval The reporting interval of this agent
    """
    environment = ReferenceField(Environment)
    node = ReferenceField(Node, required=True)
    name = StringField(required=True, unique_with=["environment", "node"])
    role = StringField(choices=ROLES, required=True)
    last_seen = DateTimeField()
    interval = IntField()

    def to_dict(self):
        return {"name": self.name,
                "role": self.role,
                "last_seen": self.last_seen.isoformat(),
                "interval": self.interval,
                "node": self.node.hostname,
                "environment": str(self.environment.id),
                }


class Resource(Document):
    """
        A resource that can be managed by an agent.

        :param environment The environment this resource is defined in
        :param resource_id The resource id of this resource

        The following parameters are derived directly from the resource_id:
        :param resource_type The type of the resource
        :param agent The agent that manages this resource (not a reference but can be used to query for agents)
        :param attribute_name The name of the identifying attribute
        :param attribute_value The value of the identifying attribute
    """
    environment = ReferenceField(Environment)
    resource_id = StringField(required=True, unique_with="agent")

    resource_type = StringField(required=True)
    agent = StringField(required=True)
    attribute_name = StringField(required=True)
    attribute_value = StringField(required=True)


ACTIONS = ("store", "push", "pull", "deploy", "dryrun", "other")
LOGLEVEL = ("INFO", "ERROR", "WARNING", "DEBUG", "TRACE")


class ResourceAction(Document):
    """
        Log related to actions performed on a specific resource version by Impera.

        :param resource_version The resource on which the actions are performed
        :param action The action performed on the resource
        :param timestamp When did the action occur
        :param message The log message associated with this action
        :param level The "urgency" of this action
        :param data A python dictionary that can be serialized to json with additional data
    """
    resource_version = ReferenceField("ResourceVersion")
    action = StringField(required=True, choices=ACTIONS)
    timestamp = DateTimeField(required=True)
    message = StringField()
    level = StringField(choices=LOGLEVEL, default="INFO")
    data = DictField()

    def to_dict(self):
        return {"action": self.action,
                "timestamp": self.timestamp.isoformat(),
                "message": self.message,
                "level": self.level,
                "data": self.data,
                }


RELEASE_STATUS = {0: "AVAILABLE", 1: "DRYRUN", 2: "DEPLOY"}
STATUS_RESULT = {0: "WAITING", 1: "IN PROGRESS", 2: "SUCCESS", 3: "ERROR"}


class ResourceVersion(Document):
    """
        A specific version of a resource. This entity contains the desired state of a resource.

        :param environment The environment this resource version is defined in
        :param rid The id of the resource and its version
        :param resource The resource for which this defines the state
        :param model The configuration model (versioned) this resource state is associated with
        :param attributes The state of this version of the resource
        :param status The deploy status of the resource
        :param status_result The result of the deploy
    """
    environment = ReferenceField(Environment, required=True)
    rid = StringField(required=True, unique_with="environment")
    resource = ReferenceField(Resource, required=True)
    model = ReferenceField("ConfigurationModel", required=True)
    attributes = MapField(DynamicField())
    status = IntField(choices=RELEASE_STATUS, default=0)
    status_result = IntField(choices=STATUS_RESULT, default=0)

    def to_dict(self):
        data = {}
        data["fields"] = {key: value for key, value in self.attributes.items()}
        data["id"] = self.rid
        data["id_fields"] = Id.parse_id(self.rid).to_dict()
        data["state"] = RELEASE_STATUS[self.status] if self.status in RELEASE_STATUS else 0
        data["result"] = STATUS_RESULT[self.status_result] if self.status_result in STATUS_RESULT else 0

        return data


class ConfigurationModel(Document):
    """
        A specific version of the configuration model.

        :param version The version of the configuration model, represented by a unix timestamp.
        :param environment The environment this configuration model is defined in
        :param created The date this configuration model was created
    """
    version = IntField(required=True, unique_with="environment")
    environment = ReferenceField(Environment)
    date = DateTimeField()
    release_status = IntField(choices=RELEASE_STATUS, default=0)
    status_result = IntField(choices=STATUS_RESULT, default=0)

    def to_dict(self):
        return {"version": self.version,
                "environment": str(self.environment.id),
                "date": self.date.isoformat(),
                "release_status": RELEASE_STATUS[self.release_status] if self.release_status in RELEASE_STATUS else 0,
                "status_result": STATUS_RESULT[self.status_result] if self.status_result in STATUS_RESULT else 0,
                }


class Code(Document):
    """
        A code deployment

        :param environment The environment this code belongs to
        :param version The version of configuration mode it belongs to
        :param sources The source code of plugins
        :param requires Python requires for the source code above
    """
    environment = ReferenceField(Environment)
    version = StringField()
    sources = DynamicField()
    requires = DynamicField()


class Repository(Document):
    """
        A Git repository that contains an Impera configuration project
    """
    url = StringField()
    branch = StringField(unique_with="url")
