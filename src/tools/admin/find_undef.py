import ast
import builtins
import sys

def find_undefined_names(filename):
    with open(filename, "r", encoding="utf-8") as f:
        code = f.read()
    
    try:
        tree = ast.parse(code)
    except Exception as e:
        print(f"{filename}: syntax error {e}")
        return
        
    class NameVisitor(ast.NodeVisitor):
        def __init__(self):
            self.defined = set(dir(builtins))
            self.used_names = set()
            self.scopes = [self.defined]
            
        def enter_scope(self):
            self.scopes.append(set())
            
        def leave_scope(self):
            self.scopes.pop()
            
        def define(self, name):
            self.scopes[-1].add(name)
            
        def is_defined(self, name):
            return any(name in scope for scope in self.scopes)

        def visit_Import(self, node):
            for alias in node.names:
                self.define(alias.asname or alias.name.split('.')[0])
            self.generic_visit(node)
            
        def visit_ImportFrom(self, node):
            for alias in node.names:
                self.define(alias.asname or alias.name)
            self.generic_visit(node)
            
        def visit_Assign(self, node):
            self.generic_visit(node.value)
            for target in node.targets:
                if isinstance(target, ast.Name):
                    self.define(target.id)
                elif isinstance(target, ast.Tuple) or isinstance(target, ast.List):
                    for elt in target.elts:
                        if isinstance(elt, ast.Name):
                            self.define(elt.id)
            
        def visit_FunctionDef(self, node):
            self.define(node.name)
            self.enter_scope()
            for arg in node.args.posonlyargs + node.args.args + node.args.kwonlyargs:
                self.define(arg.arg)
            if node.args.vararg:
                self.define(node.args.vararg.arg)
            if node.args.kwarg:
                self.define(node.args.kwarg.arg)
            self.generic_visit(node)
            self.leave_scope()

        def visit_ClassDef(self, node):
            self.define(node.name)
            self.enter_scope()
            self.generic_visit(node)
            self.leave_scope()
            
        def visit_Name(self, node):
            if isinstance(node.ctx, ast.Load):
                if not self.is_defined(node.id):
                    self.used_names.add(node.id)
            elif isinstance(node.ctx, ast.Store):
                pass
            self.generic_visit(node)

        def visit_For(self, node):
            if isinstance(node.target, ast.Name):
                self.define(node.target.id)
            elif isinstance(node.target, ast.Tuple):
                 for elt in node.target.elts:
                     if isinstance(elt, ast.Name):
                         self.define(elt.id)
            self.generic_visit(node.iter)
            self.enter_scope()
            self.generic_visit(node.body)
            self.generic_visit(node.orelse)
            self.leave_scope()

        def visit_ListComp(self, node):
             self.enter_scope()
             for gen in node.generators:
                 self.visit(gen)
             self.visit(node.elt)
             self.leave_scope()
             
        def visit_DictComp(self, node):
             self.enter_scope()
             for gen in node.generators:
                 self.visit(gen)
             self.visit(node.key)
             self.visit(node.value)
             self.leave_scope()

        def visit_SetComp(self, node):
             self.enter_scope()
             for gen in node.generators:
                 self.visit(gen)
             self.visit(node.elt)
             self.leave_scope()

        def visit_GeneratorExp(self, node):
             self.enter_scope()
             for gen in node.generators:
                 self.visit(gen)
             self.visit(node.elt)
             self.leave_scope()

        def visit_comprehension(self, node):
             if isinstance(node.target, ast.Name):
                 self.define(node.target.id)
             elif isinstance(node.target, ast.Tuple):
                 for elt in node.target.elts:
                     if isinstance(elt, ast.Name):
                         self.define(elt.id)
             self.visit(node.iter)
             for i in node.ifs:
                 self.visit(i)
                 
        def visit_ExceptHandler(self, node):
             if node.name:
                 self.define(node.name)
             self.generic_visit(node)
             
        def visit_With(self, node):
             for item in node.items:
                 self.visit(item.context_expr)
                 if item.optional_vars:
                     if isinstance(item.optional_vars, ast.Name):
                         self.define(item.optional_vars.id)
             self.generic_visit(node)

    visitor = NameVisitor()
    visitor.visit(tree)
    # Filter some false positives depending on standard scopes, globals, etc.
    if visitor.used_names:
        print(f"{filename} undefined: {visitor.used_names}")
    else:
         print(f"{filename}: OK")

for f in sys.argv[1:]:
    find_undefined_names(f)
