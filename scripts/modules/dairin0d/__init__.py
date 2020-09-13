#  ***** BEGIN GPL LICENSE BLOCK *****
#
#  This program is free software: you can redistribute it and/or modify
#  it under the terms of the GNU General Public License as published by
#  the Free Software Foundation, either version 3 of the License, or
#  (at your option) any later version.
#
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.
#
#  You should have received a copy of the GNU General Public License
#  along with this program.  If not, see <http://www.gnu.org/licenses/>.
#
#  ***** END GPL LICENSE BLOCK *****

"""
Utility modules by dairin0d
"""

if "reload" in globals():
    reload(globals(), _import_order)
else:
    _import_order = [
        "utils_python",
        "utils_math",
        "utils_text",
        "utils_accumulation",
        "bpy_inspect",
        "utils_gl",
        "utils_ui",
        "utils_blender",
        "utils_userinput",
        "utils_view3d",
        "utils_addon",
    ]
    
    def reload(G, names):
        import importlib
        package = G["__name__"] # __name__
        for name in names:
            if name in G:
                G[name] = importlib.reload(G[name])
            else:
                G[name] = importlib.import_module("."+name, package)
    
    def load(vars, items):
        G = globals()
        modules = {name:G[name] for name in _import_order}
        for mod_name, sub_names in items.items():
            module = modules[mod_name]
            if not sub_names:
                vars[mod_name] = module
            else:
                if isinstance(sub_names, str):
                    sub_names = sub_names.replace(",", " ").split()
                for sub_name in sub_names:
                    if not sub_name:
                        vars[mod_name] = module
                    else:
                        vars[sub_name] = getattr(module, sub_name)
    
    def include(path):
        import os
        import inspect
        import bpy
        
        frame = inspect.stack()[1].frame
        
        if not os.path.isabs(path):
            module_path = frame.f_globals.get("__file__") or ""
            module_name = frame.f_globals.get("__name__") or ""
            
            if module_name == "__main__":
                module_path = bpy.path.abspath("//")
            
            if module_path:
                module_path = os.path.dirname(module_path)
                path = os.path.join(module_path, path)
        
        with open(path) as f:
            code = compile(f.read(), path, 'exec')
            exec(code, frame.f_globals, frame.f_locals)
    
    try:
        reload(globals(), _import_order)
    except Exception as exc:
        # For some reason errors that happen during dairin0d importing aren't automatically printed
        import traceback
        print()
        traceback.print_exc()
        print()
