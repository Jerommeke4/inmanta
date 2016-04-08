'''
Created on Apr 4, 2016

@author: wouter
'''
from impera.execute.util import Unset
from impera.execute.proxy import UnsetException
from impera.ast import Namespace


class ResultVariable(object):

    def __init__(self, value=[]):
        self.provider = None
        self.waiters = []
        self.value = value
        self.hasValue = False
        self.type = None

    def set_type(self, type):
        self.type = type

    def set_provider(self, provider):
        self.provider = provider

    def is_ready(self):
        return self.hasValue

    def await(self, waiter):
        if self.is_ready() and not self.is_delayed():
            waiter.ready(self)
        else:
            self.waiters.append(waiter)

    def set_value(self, value, recur=True):
        if self.hasValue:
            raise Exception("Value set twice")
        if self.type is not None:
            self.type.validate(value)
        self.value = value
        self.hasValue = True
        for waiter in self.waiters:
            waiter.ready(self)

    def get_value(self):
        if not self.hasValue:
            raise UnsetException("Value not available", self)

        return self.value

    def can_get(self):
        return self.hasValue

    def is_delayed(self):
        return False

    def freeze(self):
        pass


class AttributeVariable(ResultVariable):

    def __init__(self, attribute, instance):
        self.attribute = attribute
        self.myself = instance
        ResultVariable.__init__(self)

    def set_value(self, value, recur=True):
        if self.hasValue:
            raise Exception("Value set twice")
        if self.type is not None:
            self.type.validate(value)
        self.value = value
        self.hasValue = True
        # set counterpart
        if self.attribute.end and recur:
            value.set_attribute(self.attribute.end.name, self.myself, False)
        for waiter in self.waiters:
            waiter.ready(self)


class DelayedResultVariable(ResultVariable):

    def __init__(self, queue, value=None):
        ResultVariable.__init__(self, value)
        self.queued = False
        self.queues = queue
        if self.can_get():
            self.queue()

    def freeze(self):
        if self.hasValue:
            return
        self.hasValue = True
        for waiter in self.waiters:
            waiter.ready(self)

    def queue(self):
        if self.queued:
            return
        self.queued = True
        self.queues.add_possible(self)

    def is_delayed(self):
        return True


class ListVariable(DelayedResultVariable):

    def __init__(self, attribute, instance, queue):
        self.attribute = attribute
        self.myself = instance
        DelayedResultVariable.__init__(self, queue, [])

    def set_value(self, value, recur=True):
        if self.hasValue:
            raise Exception("List modified after freeze")

        if isinstance(value, list):
            for v in value:
                self.set_value(v, recur)
            return

        if self.type is not None:
            self.type.validate(value)

        self.value.append(value)

        # set counterpart
        if self.attribute.end and recur:
            value.set_attribute(self.attribute.end.name, self.myself, False)

        if self.attribute.high is not None:
            if self.attribute.high > len(self.value):
                raise Exception("List over full: max nr of items is %d, content is %s" % (self.attribute.high, self.value))

            if self.attribute.high > len(self.value):
                self.freeze()

        if self.can_get():
            self.queue()

    def can_get(self):
        return len(self.value) >= self.attribute.low


class OptionVariable(DelayedResultVariable):

    def __init__(self, attribute, instance, queue):
        DelayedResultVariable.__init__(self, queue)
        self.value = None
        self.attribute = attribute
        self.myself = instance
        self.queue()

    def set_value(self, value, recur=True):
        if self.hasValue:
            raise Exception("Option set after freeze %s.%s = %s / %s " % (self.myself, self.attribute, value, self.value))

        if self.type is not None:
            self.type.validate(value)

        # set counterpart
        if self.attribute.end and recur:
            value.set_attribute(self.attribute.end.name, self.myself, False)

        self.value = value
        self.freeze()

    def can_get(self):
        return True

    def is_delayed(self):
        return True

waiters = []
waitersdone = []


def dumpHangs():
    for i in waiters:
        print ("Waiting", i, [(r, r.provider) for r in i.Xdepends if not r.can_get()])
    # for i in waitersdone:
    #    print ("Done", i)


class Waiter(object):

    def __init__(self, queue):
        self.waitcount = 1
        self.queue = queue
        waiters.append(self)
        self.Xdepends = []

    def await(self, waitable):
        self.waitcount = self.waitcount + 1
        self.Xdepends.append(waitable)
        waitable.await(self)

    def ready(self, other):
        self.waitcount = self.waitcount - 1
        if self.waitcount == 0:
            if self in waiters:
                waiters.remove(self)
            waitersdone.append(self)
            self.queue.add_running(self)
        if self.waitcount < 0:
            raise Exception("waitcount negative")

    def validate(self):
        waiters = [x for x in self.Xdepends if not x.can_get()]
        if len(waiters) != self.waitcount:
            print("odd: " + self)


class QueueScheduler(object):

    def __init__(self, compiler, runqueue, waitqueue):
        self.queues = {}
        self.compiler = compiler
        self.runqueue = runqueue
        self.waitqueue = waitqueue

    def set_queue(self, name, queue):
        self.queues[name] = queue

    def add_running(self, item):
        return self.runqueue.append(item)

    def add_possible(self, rv):
        return self.waitqueue.append(rv)

    def get_queue(self, queue):
        return self.queues[queue]

    def get_compiler(self):
        return self.compiler


class Resolver(object):

    def __init__(self, scopes):
        self.scopes = scopes

    def lookup(self, name):
        if "::" not in name:
            raise Exception("root resolver requesting relative name, should not occur " + name)

        parts = name.rsplit("::", 1)

        if parts[0] not in self.scopes:
            raise Exception("Namespace %s not found" % parts[0])

        return self.scopes[parts[0]].lookup(parts[1])

    def get_root_resolver(self):
        return self

    def for_namespace(self, namespace):
        return NamespaceResolver(self.scopes, namespace)


class NamespaceResolver(Resolver):

    def __init__(self, scopes, namespace):
        self.scopes = scopes
        # FIXME clean this up
        if isinstance(namespace, list):
            namespace = '::'.join(namespace)
        if isinstance(namespace, Namespace):
            namespace = namespace.get_full_name()
        if len(namespace) == 0:
            print("X")
        self.scope = scopes[namespace]

    def lookup(self, name):
        return self.scope.lookup(name)

    def for_namespace(self, namespace):
        return NamespaceResolver(self.scopes, namespace)


class ExecutionContext(object):

    def __init__(self, block, resolver):
        self.block = block
        self.slots = {n: ResultVariable() for n in block.get_variables()}
        for (n, s) in self.slots.items():
            s.set_provider(self)
        self.resolver = resolver

    def add_slot(self, name, value):
        raise Exception("depricated")
        out = ResultVariable()
        out.set_value(value)
        self.slots[name] = out

    def lookup(self, name):
        if "::" in name:
            self.resolver.lookup(name)
        if name in self.slots:
            return self.slots[name]
        return self.resolver.lookup(name)

    def emit(self, queue):
        self.block.emit(self, queue)

    def get_root_resolver(self):
        return self.resolver.get_root_resolver()

    def for_namespace(self, namespace):
        return self.resolver.get_root_resolver().for_namespace(namespace)


class WaitUnit(Waiter):

    def __init__(self, queue_scheduler, resolver, require, resumer):
        Waiter.__init__(self, queue_scheduler)
        self.queue_scheduler = queue_scheduler
        self.resolver = resolver
        self.require = require
        self.resumer = resumer
        if isinstance(require, dict):
            for (s, r) in require.items():
                self.await(r)
        else:
            self.await(require)
        self.ready(self)

    def execute(self):
        self.resumer.resume(self.require, self.resolver, self.queue_scheduler)


class HangUnit(Waiter):

    def __init__(self, queue_scheduler, resolver, requires, target, resumer):
        Waiter.__init__(self, queue_scheduler)
        self.queue_scheduler = queue_scheduler
        self.resolver = resolver
        self.requires = requires
        self.resumer = resumer
        self.target = target
        for (s, r) in requires.items():
            self.await(r)
        self.ready(self)

    def execute(self):
        self.resumer.resume({k: v.get_value()
                             for (k, v) in self.requires.items()}, self.resolver, self.queue_scheduler, self.target)


class ExecutionUnit(Waiter):

    def __init__(self, queue_scheduler, resolver, result: ResultVariable, requires, expression):
        Waiter.__init__(self, queue_scheduler)
        self.result = result
        result.set_provider(self)
        self.requires = requires
        self.expression = expression
        self.resolver = resolver
        self.queue_scheduler = queue_scheduler
        for (s, r) in requires.items():
            self.await(r)
        self.ready(self)

    def execute(self):
        requires = {k: v.get_value() for (k, v) in self.requires.items()}
        value = self.expression.execute(requires, self.resolver, self.queue_scheduler)
        self.result.set_value(value)

    def __repr__(self):
        return repr(self.expression)


class Instance(ExecutionContext):

    def __init__(self, type, resolver, queue):
        self.resolver = resolver.get_root_resolver()
        self.type = type
        self.slots = {n: type.get_attribute(n).get_new_Result_Variable(self, queue) for n in type.get_all_attribute_names()}
        self.slots["self"] = ResultVariable()
        self.slots["self"].set_value(self)
        self.sid = id(self)

    def get_type(self):
        return self.type

    def set_attribute(self, name, value, recur=True):
        if name not in self.slots:
            raise Exception("could not find variable with name: %s in type %s" % (name, repr(self.type)))
        self.slots[name].set_value(value, recur)

    def get_attribute(self, name):
        try:
            return self.slots[name]
        except KeyError:
            raise Exception("Attribute %s does not exist for %s" % (name, self.type))

    def __repr__(self):
        return "%s %02x" % (self.type, self.sid)

    def final(self):
        """ the object should be complete, freeze all attributes"""
        for (n, v) in self.slots.items():
            if not v.is_ready():
                if v.can_get():
                    v.freeze()
                else:
                    self.dump()
                    raise Exception("Object can not be frozen: " + str(self))

    def dump(self):
        print("------------ ")
        print(str(self))
        print("------------ ")
        for (n, v) in self.slots.items():
            if(v.can_get()):

                value = v.value
                print("%s\t\t%s" % (n, value))
            else:
                print("BAD: %s\t\t%s" % (n, v.provider))

    def verify_done(self):
        for (n, v) in self.slots.items():
            if not v.can_get():
                return False
        return True
