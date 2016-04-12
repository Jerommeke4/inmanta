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

from impera.execute.util import Unset, Unknown
from impera.stats import Stats
from impera.execute.runtime import ResultVariable, ListVariable, OptionVariable, AttributeVariable


class Attribute(object):
    """
        The attribute base class for entity attributes.

        @param entity: The entity this attribute belongs to
    """

    def __init__(self, entity, value_type, name):
        self.__name = name  # : String

        entity.add_attribute(self)
        self.__entity = entity
        self.__type = value_type

    def get_type(self):
        """
            Get the type of this data item
        """
        return self.__type

    type = property(get_type)

    def get_name(self):
        """
            Get the name of the attribute. This is the name this attribute
            is associated with in the entity.
        """
        return self.__name

    name = property(get_name)

    def __hash__(self):
        """
            The hash of this object is based on the name of the attribute
        """
        return hash(self.__name)

    def __repr__(self):
        return self.__name

    def get_entity(self):
        """
            Return the entity this attribute belongs to
        """
        return self.__entity

    entity = property(get_entity)

    def validate(self, value):
        """
            Validate a value that is going to be assigned to this attribute
        """
        if (not hasattr(value, "is_unknown") or not value.is_unknown()) and not isinstance(value, Unknown):
            self.type.validate(value)

    def set_attribute(self, instance, value):
        """
            Set a value to this attribute on instance
        """
        if (self.name in instance._attributes and instance._attributes[self.name] is not None
                and instance._attributes[self.name] != value):
            raise Exception("Attribute %s.%s can only be set once. Current value is %s, new value %s." %
                            (instance, self.name, instance._attributes[self.name], value))

        try:
            value.validate(self.validate)
        except ValueError:
            raise ValueError("Invalid value %s for attribute %s" % (value, self.name))

        instance._attributes[self.name] = value
        Stats.get("set attribute").increment()

    def get_new_Result_Variable(self, instance, queue):
        out = ResultVariable()
        out.set_type(self.__type)
        out.set_provider(instance)
        return out


class RelationAttribute(Attribute):
    """
        An attribute that is a relation
    """

    def __init__(self, entity, value_type, name):
        Attribute.__init__(self, entity, value_type, name)
        self.end = None
        self.low = 1
        self.high = 1
        self.depends = False

    def __repr__(self):
        return "[%s:%s] %s" % (self.low, self.high, self.name)

    def set_multiplicity(self, values):
        """
            Set the multiplicity of this end
        """
        self.low = values[0]
        self.high = values[1]

    def set_attribute(self, instance, value, double=True):
        """
            Set value to this attribute on instance

            @param double: Make a double binding of the relation
        """
        if self.low == 1 and self.high == 1:
            # this relation is handled as a normal attribute
            Attribute.set_attribute(self, instance, value)

        else:
            if (self.name not in instance._attributes or instance._attributes[self.name] is None
                    or isinstance(instance._attributes[self.name], Unset)):
                # initialize the attribute to a variable with an empty list
                current_value = Variable(list())
                instance._attributes[self.name] = current_value

            else:
                current_value = instance._attributes[self.name]

            num_items = len(current_value.value)
            if self.high is not None and num_items + 1 > self.high:
                raise Exception("%s: This relation has a maximum multiplicity of %d" % (self, self.high))

            value.validate(self.validate)

            # store the value if it not yet in the list
            if value.value not in current_value.value:
                current_value.value.append(value.value)

            else:
                double = False

        # set the other side on value
        if double:
            Stats.get("set relation").increment()
            self.end.set_attribute(value.value, Variable(instance), False)

    def get_new_Result_Variable(self, instance, queue):
        if self.low == 1 and self.high == 1:
            out = AttributeVariable(self, instance)
        elif self.low == 0 and self.high == 1:
            out = OptionVariable(self, instance, queue)
        else:
            out = ListVariable(self,  instance, queue)
        out.set_type(self.get_type())
        out.set_provider(self)
        return out
