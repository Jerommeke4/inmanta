"""
    Copyright 2016 Inmanta

    Licensed under the Apache License, Version 2.0 (the "License");
    you may not use this file except in compliance with the License.
    You may obtain a copy of the License at

        http://www.apache.org/licenses/LICENSE-2.0

    Unless required by applicable law or agreed to in writing, software
    distributed under the License is distributed on an "AS IS" BASIS,
    WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
    See the License for the specific language governing permissions and
    limitations under the License.

    Contact: code@inmanta.com
"""
import logging


from inmanta import protocol
from inmanta.config import Config
from inmanta.execute.util import Unknown
from inmanta.export import unknown_parameters
from inmanta import resources
from tornado.ioloop import IOLoop

LOGGER = logging.getLogger(__name__)


def get_fact(res, fact_name: str, default_value=None, metadata={}) -> "any":
    """
        Get the fact with the given name from the database
    """
    resource_id = resources.to_id(res)

    fact_value = None
    try:
        client = protocol.Client("compiler")

        env = Config.get("config", "environment", None)
        if env is None:
            raise Exception("The environment of this model should be configured in config>environment")

        def call():
            return client.get_param(tid=env, id=fact_name, resource_id=resource_id)

        result = IOLoop.current().run_sync(call, 5)

        if result.code == 200:
            fact_value = result.result["parameter"]["value"]
        else:
            LOGGER.debug("Param %s of resource %s is unknown", fact_name, resource_id)
            fact_value = Unknown(source=res)
            unknown_parameters.append({"resource": resource_id, "parameter": fact_name, "source": "fact"})
    except ConnectionRefusedError:
        fact_value = Unknown(source=res)
        unknown_parameters.append({"resource": resource_id, "parameter": fact_name, "source": "fact"})

    if isinstance(fact_value, Unknown) and default_value is not None:
        return default_value

    return fact_value
