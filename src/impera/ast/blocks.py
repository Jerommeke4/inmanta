from impera.ast.statements import Statement
from impera.ast.statements.assign import Assign
from impera.ast.type import NameSpacedResolver
from impera.execute.runtime import ExecutionContext


class BasicBlock(object):

    def __init__(self, namespace):
        self.stmts = []
        self.variables = []
        self.namespace = namespace

    def add(self, stmt: Statement):
        self.stmts.append(stmt)

    def get_variables(self):
        return self.variables

    def add_var(self, name):
        self.variables.append(name)

    def normalize(self, resolver: NameSpacedResolver):
        resolver = resolver.get_resolver_for(self.namespace)

        assigns = [s for s in self.stmts if isinstance(s, Assign)]
        self.variables = [s.name.full_name for s in assigns]

        for s in self.stmts:
            s.normalize(resolver)

        self.requires = set([require for s in self.stmts for require in s.requires()])

        self.external = self.requires - set(self.variables)

        self.external_not_global = [x for x in self.external if "::" not in x]

    def get_requires(self):
        return self.external

    def emit(self, resolver, queue):
        for s in self.stmts:
            s.emit(resolver, queue)
