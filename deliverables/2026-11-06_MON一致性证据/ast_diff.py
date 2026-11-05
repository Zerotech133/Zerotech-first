import ast, sys, json
def sigs(path):
    src = open(path, encoding='utf-8-sig').read()
    t = ast.parse(src)
    out = {}
    for n in t.body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            a = n.args
            args = [x.arg for x in a.posonlyargs] + [x.arg for x in a.args] + (['*'+a.vararg.arg] if a.vararg else []) + [x.arg for x in a.kwonlyargs] + (['**'+a.kwarg.arg] if a.kwarg else [])
            out[n.name] = 'def(' + ','.join(args) + ')'
        elif isinstance(n, ast.ClassDef):
            out[n.name] = 'class'
        elif isinstance(n, ast.Assign):
            for tg in n.targets:
                if isinstance(tg, ast.Name):
                    out[tg.id] = 'var'
    return out
m = sigs(r'C:\Temp\mon_consistency_20261106\monitor_master.py')
b = sigs(r'C:\Temp\mon_consistency_20261106\monitor_monbranch.py')
only_m = sorted(set(m)-set(b)); only_b = sorted(set(b)-set(m))
changed = sorted(k for k in set(m)&set(b) if m[k]!=b[k])
print(json.dumps({'only_master': only_m, 'only_monbranch': only_b, 'signature_changed': {k:[m[k],b[k]] for k in changed}}, ensure_ascii=False, indent=1))
