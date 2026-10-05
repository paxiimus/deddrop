"""Static cross-reference checks using only stdlib ast — no Django needed,
so this runs even without the real dependencies installed. Catches classes
of bug py_compile structurally cannot, because none of them are syntax
errors — all three would be an immediate AttributeError/ImportError (or
silent data-loss on a real migrate) the moment Django actually boots:

  1. A settings.X reference where X was never actually defined.
  2. A `from .module import Y` where Y doesn't exist in that module.
  3. Migration drift — models.py has fields no migration ever added, or
     migrations add fields models.py no longer has. This one exists
     because it was flagged as an explicit unverified assumption early in
     this project ("run makemigrations --check on first deploy") and
     never actually checked, across several rounds of models.py edits
     since. It found nothing wrong the one time it mattered so far — but
     found two real bugs in an earlier draft of this exact script first,
     which is worth remembering before trusting any static checker's
     silence, this one included.

Run via `make verify`.
"""
import ast
import os

API_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def module_level_names(tree):
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return names


def check_settings_and_imports():
    settings_path = f"{API_DIR}/deaddrop/settings.py"
    defined_settings = module_level_names(ast.parse(open(settings_path).read()))
    # Real Django settings this project references but never assigns
    # itself — they come from Django's own global_settings defaults.
    DJANGO_BUILTINS = {"AUTH_PASSWORD_VALIDATORS"}

    errors = []
    for root, _, files in os.walk(API_DIR):
        if "migrations" in root or "__pycache__" in root or "scripts" in root:
            continue
        for fname in files:
            if not fname.endswith(".py"):
                continue
            path = os.path.join(root, fname)
            tree = ast.parse(open(path).read())
            for node in ast.walk(tree):
                if (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
                        and node.value.id == "settings"):
                    attr = node.attr
                    if attr not in defined_settings and attr not in DJANGO_BUILTINS:
                        errors.append(f"{path}: settings.{attr} referenced but never defined in settings.py")

            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.level == 1 and node.module:
                    target_path = os.path.join(root, node.module.replace(".", "/") + ".py")
                    if not os.path.exists(target_path):
                        continue
                    target_names = module_level_names(ast.parse(open(target_path).read()))
                    for alias in node.names:
                        if alias.name != "*" and alias.name not in target_names:
                            errors.append(f"{path}: imports '{alias.name}' from .{node.module}, but it's not defined there")
    return errors


def _get_call_kwargs(call_node):
    return {kw.arg: kw.value for kw in call_node.keywords if kw.arg}


def _get_str(node):
    return node.value if isinstance(node, ast.Constant) else None


def _is_field_assignment(value_node):
    if not isinstance(value_node, ast.Call):
        return False
    func = value_node.func
    name = func.attr if isinstance(func, ast.Attribute) else (func.id if isinstance(func, ast.Name) else "")
    return name.endswith("Field") or name == "ForeignKey"


def _simulate_migrations(app_dir, migration_files):
    models = {}
    for fname in migration_files:
        tree = ast.parse(open(os.path.join(app_dir, "migrations", fname)).read())
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
                continue
            op = node.func.attr
            kwargs = _get_call_kwargs(node)

            if op == "CreateModel":
                name = _get_str(kwargs.get("name"))
                fields_arg = kwargs.get("fields")
                field_names = set()
                if isinstance(fields_arg, ast.List):
                    for elt in fields_arg.elts:
                        if isinstance(elt, ast.Tuple) and len(elt.elts) >= 1:
                            fname_str = _get_str(elt.elts[0])
                            if fname_str and fname_str != "id":  # implicit PK — see actual_model_fields
                                field_names.add(fname_str)
                if name:
                    # Lowercased: Django's own convention is that
                    # AddField/RemoveField/AlterField reference model_name
                    # in lowercase, while CreateModel uses the real class
                    # name — a case-sensitive lookup here would silently
                    # miss every AddField that follows.
                    models[name.lower()] = field_names

            elif op == "AddField":
                model, field = _get_str(kwargs.get("model_name")), _get_str(kwargs.get("name"))
                if model in models and field:
                    models[model].add(field)

            elif op == "RemoveField":
                model, field = _get_str(kwargs.get("model_name")), _get_str(kwargs.get("name"))
                if model in models:
                    models[model].discard(field)
    return models


def _actual_model_fields(models_py_path):
    tree = ast.parse(open(models_py_path).read())
    result = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            fields = set()
            for stmt in node.body:
                if isinstance(stmt, ast.Assign) and _is_field_assignment(stmt.value):
                    for target in stmt.targets:
                        if isinstance(target, ast.Name) and target.id != "id":  # implicit PK, asymmetric with migrations on purpose
                            fields.add(target.id)
            if fields:
                result[node.name.lower()] = fields
    return result


def check_migration_drift():
    # Migration files are discovered, not listed — a hardcoded list silently
    # skipped any migration nobody remembered to add here.
    def _migration_files(app):
        d = f"{API_DIR}/{app}/migrations"
        return sorted(f for f in os.listdir(d) if f[:4].isdigit() and f.endswith(".py"))

    apps = {app: _migration_files(app) for app in ("drops", "identities")}
    errors = []
    for app, migration_files in apps.items():
        app_dir = f"{API_DIR}/{app}"
        simulated = _simulate_migrations(app_dir, migration_files)
        actual = _actual_model_fields(f"{app_dir}/models.py")
        for model_name, actual_fields in actual.items():
            if model_name not in simulated:
                errors.append(f"{app}.{model_name}: has fields but no CreateModel found in the tracked migration list — update the `apps` dict above if this is a new model")
                continue
            sim_fields = simulated[model_name]
            missing = actual_fields - sim_fields
            extra = sim_fields - actual_fields
            if missing:
                errors.append(f"{app}.{model_name}: models.py has {sorted(missing)} that no migration ever adds")
            if extra:
                errors.append(f"{app}.{model_name}: migrations add {sorted(extra)} but models.py no longer has them")
    return errors


def check_throttle_scopes():
    """Every throttle class's `scope = "..."` needs a matching entry in
    settings.DEFAULT_THROTTLE_RATES, or DRF's get_rate() raises
    ImproperlyConfigured the moment that throttle actually runs — not a
    syntax error, so py_compile can't catch it, and easy to introduce
    silently when adding a new throttle class without remembering the
    settings.py side."""
    # Both files that actually define throttle classes — identities/ was
    # added after this check was first written, and hardcoding just
    # drops/throttles.py would have left a real blind spot for exactly
    # the class of bug this check exists to catch, just in the one place
    # it wasn't looking.
    scopes = set()
    for app in ("drops", "identities"):
        tree = ast.parse(open(f"{API_DIR}/{app}/throttles.py").read())
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id == "scope" and isinstance(node.value, ast.Constant):
                        scopes.add(node.value.value)

    settings_tree = ast.parse(open(f"{API_DIR}/deaddrop/settings.py").read())
    rate_keys = set()
    for node in ast.walk(settings_tree):
        if isinstance(node, ast.Dict):
            for key in node.keys:
                if isinstance(key, ast.Constant) and isinstance(key.value, str):
                    rate_keys.add(key.value)

    return [f"throttles.py: scope '{s}' has no matching entry in DEFAULT_THROTTLE_RATES" for s in scopes if s not in rate_keys]


if __name__ == "__main__":
    all_errors = check_settings_and_imports() + check_migration_drift() + check_throttle_scopes()
    if all_errors:
        print(f"FOUND {len(all_errors)} issue(s):")
        for e in all_errors:
            print(" -", e)
        raise SystemExit(1)
    print("Clean — settings/import cross-references, migration-vs-model field drift, and throttle scope/rate")
    print("matching all checked. This is real static verification, not just syntax checking, but it's still")
    print("not Django actually running. It cannot catch runtime logic errors, query correctness, or anything")
    print("that only shows up when the app actually boots against a real database.")
