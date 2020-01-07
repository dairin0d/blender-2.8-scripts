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

import time

import bpy
import bmesh

import mathutils
from mathutils import Color, Vector, Euler, Quaternion, Matrix

from .bpy_inspect import BlEnums

class Selection:
    def __init__(self, context=None, mode=None, elem_types=None, container=set, brute_force_update=False, pose_bones=True, copy_bmesh=False):
        self.context = context
        self.mode = mode
        self.elem_types = elem_types
        self.brute_force_update = brute_force_update
        self.pose_bones = pose_bones
        self.copy_bmesh = copy_bmesh
        # In some cases, user might want a hashable type (e.g. frozenset or tuple)
        self.container = container
        # We MUST keep reference to bmesh, or it will be garbage-collected
        self.bmesh = None
    
    def get_context(self):
        context = self.context or bpy.context
        mode = self.mode or context.mode
        active_obj = context.active_object
        actual_mode = BlEnums.mode_from_object(active_obj)
        mode = BlEnums.normalize_mode(mode, active_obj)
        if not BlEnums.is_mode_valid(mode, active_obj):
            mode = None # invalid request
        return context, active_obj, actual_mode, mode
    
    @property
    def normalized_mode(self):
        return self.get_context()[-1]
    
    @property
    def stateless_info(self):
        history, active, total = next(self.walk(), (None,None,0))
        active_id = active.name if hasattr(active, "name") else hash(active)
        return (total, active_id)
    
    @property
    def active(self):
        return next(self.walk(), (None,None))[1]
    @active.setter
    def active(self, value):
        self.update_active(value)
    
    @property
    def history(self):
        return next(self.walk(), (None,))[0]
    @history.setter
    def history(self, value):
        self.update_history(value)
    
    @property
    def selected(self):
        walker = self.walk()
        next(walker, None) # skip active item
        return dict(item for item in walker if item[1])
    @selected.setter
    def selected(self, value):
        self.update(value)
    
    def __iter__(self):
        walker = self.walk()
        next(walker, None) # skip active item
        for item in walker:
            if item[1]: yield item
    
    def __bool__(self):
        """Returns True if there is at least 1 element selected"""
        context, active_obj, actual_mode, mode = self.get_context()
        if not mode: return False
        
        if mode == 'OBJECT':
            return bool(context.selected_objects)
        elif mode == 'EDIT_MESH':
            mesh = active_obj.data
            if actual_mode == 'EDIT_MESH':
                return bool(mesh.total_vert_sel)
            else:
                return any(item.select for item in mesh.vertices)
        elif mode in {'EDIT_CURVE', 'EDIT_SURFACE'}:
            for spline in active_obj.data.splines:
                for item in spline.bezier_points:
                    if (item.select_control_point or
                        item.select_left_handle or
                        item.select_right_handle):
                        return True
                for item in spline.points:
                    if item.select:
                        return True
        elif mode == 'EDIT_METABALL':
            return bool(active_obj.data.elements.active)
        elif mode == 'EDIT_LATTICE':
            return any(item.select for item in active_obj.data.points)
        elif mode == 'EDIT_ARMATURE':
            return any(item.select_head or item.select_tail
                       for item in active_obj.data.edit_bones)
        elif mode == 'POSE':
            return any(item.select for item in active_obj.data.bones)
        elif mode == 'PARTICLE':
            # Theoretically, particle keys can be selected,
            # but there seems to be no API for working with this
            pass
        else:
            pass # no selectable elements in other modes
        
        return False
    
    def walk(self):
        """Iterates over selection, returning (history, active, count) first, then (element, selected_attributes) until exhausted"""
        context, active_obj, actual_mode, mode = self.get_context()
        if not mode: return
        
        container = self.container
        sel_map = {False: container(), True: container(("select",))}
        
        if mode != 'EDIT_MESH': self.bmesh = None
        
        if mode == 'OBJECT':
            total = len(context.selected_objects)
            item = active_obj
            yield ([], item, total)
            
            select = sel_map[True] # selected by definition
            for item in context.selected_objects:
                if not (item and item.name): return # object deleted (state disrupted)
                yield (item, select)
        elif mode == 'EDIT_MESH':
            mesh = active_obj.data
            elem_types = self.elem_types
            if actual_mode == 'EDIT_MESH':
                if self.copy_bmesh:
                    self.bmesh = bmesh.from_edit_mesh(mesh).copy()
                else:
                    if not (self.bmesh and self.bmesh.is_valid):
                        self.bmesh = bmesh.from_edit_mesh(mesh)
                bm = self.bmesh
                
                item = bm.faces.active
                
                if mesh.total_vert_sel == 0: # non-0 only in Edit mode
                    yield ([], item, 0)
                    return
                
                # No, by default all selected elements should be returned!
                #if not elem_types:
                #    elem_types = bm.select_mode
                
                colls = []
                if (not elem_types) or ('FACE' in elem_types):
                    colls.append(bm.faces)
                if (not elem_types) or ('EDGE' in elem_types):
                    colls.append(bm.edges)
                if (not elem_types) or ('VERT' in elem_types):
                    colls.append(bm.verts)
                
                total = sum(len(items) for items in colls)
                if bm.select_history:
                    yield (list(bm.select_history), item, total)
                else:
                    yield ([], item, total)
                
                for items in colls:
                    for item in items:
                        if not item.is_valid:
                            self.bmesh = None
                            return
                        yield (item, sel_map[item.select])
            else:
                self.bmesh = None
                
                colls = []
                if (not elem_types) or ('FACE' in elem_types):
                    colls.append(mesh.polygons)
                if (not elem_types) or ('EDGE' in elem_types):
                    colls.append(mesh.edges)
                if (not elem_types) or ('VERT' in elem_types):
                    colls.append(mesh.vertices)
                
                total = sum(len(items) for items in colls)
                item = None
                if mesh.polygons.active >= 0:
                    item = mesh.polygons[mesh.polygons.active]
                yield ([], item, total)
                
                for items in colls:
                    for item in items:
                        yield (item, sel_map[item.select])
        elif mode in {'EDIT_CURVE', 'EDIT_SURFACE'}:
            total = sum(len(spline.bezier_points) + len(spline.points)
                for spline in active_obj.data.splines)
            yield ([], None, total)
            
            bezier_sel_map = {
                (False, False, False): container(),
                (True, False, False): container(("select_left_handle",)),
                (False, True, False): container(("select_control_point",)),
                (False, False, True): container(("select_right_handle",)),
                (True, True, False): container(("select_left_handle", "select_control_point")),
                (False, True, True): container(("select_control_point", "select_right_handle")),
                (True, False, True): container(("select_left_handle", "select_right_handle")),
                (True, True, True): container(("select_left_handle", "select_control_point", "select_right_handle")),
            }
            
            # It seems like the only way the validity of spline can be determined
            # is to check if path_from_id() returns empty string.
            # However, it also seems that Blender does not crash when trying to
            # access deleted splines or their points.
            for spline in active_obj.data.splines:
                for item in spline.bezier_points:
                    yield (item, bezier_sel_map[(item.select_left_handle, item.select_control_point, item.select_right_handle)])
                
                for item in spline.points:
                    yield (item, sel_map[item.select])
        elif mode == 'EDIT_METABALL':
            total = 1 # only active is known in current API
            item = active_obj.data.elements.active
            yield ([], item, total)
            
            # We don't even know if active element is actually selected
            # Just assume it is, to have at least some information
            #yield (item, container())
            yield (item, sel_map[True])
        elif mode == 'EDIT_LATTICE':
            total = len(active_obj.data.points)
            yield ([], None, total)
            
            for item in active_obj.data.points:
                yield (item, sel_map[item.select])
        elif mode == 'EDIT_ARMATURE':
            total = len(active_obj.data.edit_bones)
            item = active_obj.data.edit_bones.active
            yield ([], item, total)
            
            editbone_sel_map = {
                (False, False, False): container(),
                (True, False, False): container(("select_head",)),
                (False, True, False): container(("select",)),
                (False, False, True): container(("select_tail",)),
                (True, True, False): container(("select_head", "select")),
                (False, True, True): container(("select", "select_tail")),
                (True, False, True): container(("select_head", "select_tail")),
                (True, True, True): container(("select_head", "select", "select_tail")),
            }
            
            for item in active_obj.data.edit_bones:
                if not (item and item.name): return # object deleted (state disrupted)
                yield (item, editbone_sel_map[(item.select_head, item.select, item.select_tail)])
        elif mode == 'POSE':
            total = len(active_obj.data.bones)
            item = active_obj.data.bones.active
            
            if self.pose_bones:
                pose_bones = active_obj.pose.bones
                
                pb = (pose_bones.get(item.name) if item else None)
                yield ([], pb, total)
                
                for item in active_obj.data.bones:
                    if not (item and item.name): return # object deleted (state disrupted)
                    yield (pose_bones.get(item.name), sel_map[item.select])
            else:
                yield ([], item, total)
                
                for item in active_obj.data.bones:
                    if not (item and item.name): return # object deleted (state disrupted)
                    yield (item, sel_map[item.select])
        elif mode == 'PARTICLE':
            # Theoretically, particle keys can be selected,
            # but there seems to be no API for working with this
            pass
        else:
            pass # no selectable elements in other modes
    
    def update_active(self, item):
        context, active_obj, actual_mode, mode = self.get_context()
        if not mode: return
        
        if mode == 'OBJECT':
            context.view_layer.objects.active = item
        elif mode == 'EDIT_MESH':
            mesh = active_obj.data
            if actual_mode == 'EDIT_MESH':
                bm = self.bmesh or bmesh.from_edit_mesh(mesh)
                self.bmesh = bm
                bm.faces.active = item
            else:
                mesh.polygons.active = (item.index if item else -1)
        elif mode in {'EDIT_CURVE', 'EDIT_SURFACE'}:
            pass # no API for active element
        elif mode == 'EDIT_METABALL':
            active_obj.data.elements.active = item
        elif mode == 'EDIT_LATTICE':
            pass # no API for active element
        elif mode == 'EDIT_ARMATURE':
            active_obj.data.edit_bones.active = item
        elif mode == 'POSE':
            if item: item = active_obj.data.bones.get(item.name)
            active_obj.data.bones.active = item
        elif mode == 'PARTICLE':
            # Theoretically, particle keys can be selected,
            # but there seems to be no API for working with this
            pass
        else:
            pass # no selectable elements in other modes
    
    def update_history(self, history):
        context, active_obj, actual_mode, mode = self.get_context()
        if not mode: return
        
        if mode == 'EDIT_MESH':
            mesh = active_obj.data
            if actual_mode == 'EDIT_MESH':
                bm = self.bmesh or bmesh.from_edit_mesh(mesh)
                self.bmesh = bm
                
                bm.select_history.clear()
                for item in history:
                    bm.select_history.add(item)
                #bm.select_history.validate()
            else:
                pass # history not supported
        else:
            pass # history not supported
    
    def __update_strategy(self, is_actual_mode, data, expr_info):
        # We use select_all(action) only when the context is right
        # and iterating over all objects can be avoided.
        select_all_action = None
        if not is_actual_mode: return select_all_action, data
        
        operation, new_toggled, invert_new, old_toggled, invert_old = expr_info
        
        # data = {} translates to "no exceptions"
        if operation == 'SET':
            if new_toggled is False:
                select_all_action = 'DESELECT'
                data = {} # False --> False
            elif new_toggled is True:
                select_all_action = 'SELECT'
                data = {} # True --> True
            elif invert_new:
                select_all_action = 'SELECT'
            else:
                select_all_action = 'DESELECT'
        elif operation == 'OR':
            if new_toggled is False:
                if old_toggled is False:
                    select_all_action = 'DESELECT'
                    data = {} # False OR False --> False
                elif old_toggled is True:
                    select_all_action = 'SELECT'
                    data = {} # True OR False --> True
                else:
                    data = {} # x OR False --> x
            elif new_toggled is True:
                select_all_action = 'SELECT'
                data = {} # x OR True --> True
            elif invert_new:
                pass # need to iterate over all objects anyway
            else:
                if invert_old:
                    select_all_action = 'INVERT'
                else:
                    select_all_action = '' # use data, but no select_all
        elif operation == 'AND':
            if new_toggled is False:
                select_all_action = 'DESELECT'
                data = {} # x AND False --> False
            elif new_toggled is True:
                if old_toggled is False:
                    select_all_action = 'DESELECT'
                    data = {} # False AND False --> False
                elif old_toggled is True:
                    select_all_action = 'DESELECT'
                    data = {} # True AND False --> False
                else:
                    data = {} # x AND True --> x
            elif invert_new:
                if invert_old:
                    select_all_action = 'INVERT'
                else:
                    select_all_action = '' # use data, but no select_all
            else:
                pass # need to iterate over all objects anyway
        elif operation == 'XOR':
            if new_toggled is False:
                if old_toggled is False:
                    select_all_action = 'DESELECT'
                    data = {} # False != False --> False
                elif old_toggled is True:
                    select_all_action = 'SELECT'
                    data = {} # True != False --> True
            elif new_toggled is True:
                if old_toggled is False:
                    select_all_action = 'SELECT'
                    data = {} # False != True --> True
                elif old_toggled is True:
                    select_all_action = 'DESELECT'
                    data = {} # True != True --> False
            elif invert_new:
                pass # need to iterate over all objects anyway
            else:
                pass # need to iterate over all objects anyway
        
        return select_all_action, data
    
    def __update_make_selector_expression(self, name, use_kv, expr_info):
        operation, new_toggled, invert_new, old_toggled, invert_old = expr_info
        
        data_code = ("value" if use_kv else "data.get(item, '')")
        
        if not isinstance(name, str):
            name, code_get, code_set = name
        else:
            code_get, code_set = None, None
        
        if new_toggled is not None:
            code_new = repr(new_toggled)
        elif invert_new:
            code_new = f"({repr(name)} not in {data_code})"
        else:
            code_new = f"({repr(name)} in {data_code})"
        
        if old_toggled is not None:
            code_old = repr(old_toggled)
        elif invert_old:
            if code_get:
                code_old = f"(not {code_get.format('item')})"
            else:
                code_old = f"(not item.{name})"
        else:
            if code_get:
                code_old = code_get.format("item")
            else:
                code_old = f"item.{name}"
        
        if operation == 'OR':
            code = f"{code_old} or {code_new}"
        elif operation == 'AND':
            code = f"{code_old} and {code_new}"
        elif operation == 'XOR':
            code = f"{code_old} != {code_new}"
        else:
            code = code_new # SET
        
        if code_set:
            return code_set.format("item", f"({code})")
        else:
            return f"item.{name} = ({code})"
    
    def __update_make_selector(self, build_infos, expr_info):
        tab = "    "
        expr_maker = self.__update_make_selector_expression
        localvars = {"isinstance":isinstance}
        type_cnt = 0
        lines = ["def apply(*args, data=None, context=None):"]
        for i, build_info in enumerate(build_infos):
            use_kv = build_info.get("use_kv", False)
            item_map = build_info.get("item_map", None)
            type_names = build_info["names"]
            
            expr_tab = tab*2
            
            if item_map: lines.append(tab + f"item_map = {item_map}")
            
            if use_kv:
                lines.append(tab + f"for item, value in args[{i}].items():")
                lines.append(expr_tab + "if not item: continue")
            else:
                lines.append(tab + f"for item in args[{i}]:")
            
            if item_map:
                lines.append(expr_tab + "item = item_map.get(item.name)")
                lines.append(expr_tab + "if not item: continue")
            
            if len(type_names) < 2:
                item_type, names = type_names[0]
                
                if (not use_kv) and (len(names) > 1):
                    lines.append(expr_tab + "value = data.get(item, '')")
                    use_kv = True
                
                for name in names:
                    lines.append(expr_tab + expr_maker(name, use_kv, expr_info))
            else:
                tab_if = expr_tab
                expr_tab += tab
                j = 0
                for item_type, names in type_names:
                    j += 1
                    type_name = f"type{type_cnt}"
                    type_cnt += 1
                    localvars[type_name] = item_type
                    
                    if j == 1:
                        lines.append(tab_if + f"if isinstance(item, {type_name}):")
                    elif j < len(type_names):
                        lines.append(tab_if + f"elif isinstance(item, {type_name}):")
                    else:
                        lines.append(tab_if + "else:")
                    
                    for name in names:
                        lines.append(expr_tab + expr_maker(name, use_kv, expr_info))
        
        code = "\n".join(lines)
        #print(code.strip())
        exec(code, localvars, localvars)
        return localvars["apply"]
    
    __cached_selectors = {}
    
    def update(self, data, operation='SET'):
        if not isinstance(data, dict):
            raise ValueError("data must be a dict")
        
        toggle_old = operation.startswith("^")
        invert_old = operation.startswith("!")
        toggle_new = operation.endswith("^")
        invert_new = operation.endswith("!")
        operation = operation.replace("!", "").replace("^", "")
        
        if operation not in {'SET', 'OR', 'AND', 'XOR'}:
            raise ValueError("operation must be one of {'SET', 'OR', 'AND', 'XOR'}")
        
        context, active_obj, actual_mode, mode = self.get_context()
        if not mode: return
        
        new_toggled = (not any(data.values()) if toggle_new else None)
        old_toggled = (not bool(self) if toggle_old else None)
        
        expr_info = (operation, new_toggled, invert_new, old_toggled, invert_old)
        
        is_actual_mode = (mode == actual_mode)
        if self.brute_force_update:
            select_all_action = None
        else:
            select_all_action, data = self.__update_strategy(is_actual_mode, data, expr_info)
        #print("Strategy: action={}, data={}".format(repr(select_all_action), bool(data)))
        use_brute_force = select_all_action is None
        
        def make_selector(*build_infos):
            selector_key = (mode, is_actual_mode, use_brute_force, expr_info)
            selector = Selection.__cached_selectors.get(selector_key)
            #print(selector_key)
            if selector is None:
                selector = self.__update_make_selector(build_infos, expr_info)
                Selection.__cached_selectors[selector_key] = selector
            #print(selector)
            return selector
        
        if mode == 'OBJECT':
            if select_all_action:
                bpy.ops.object.select_all(action=select_all_action)
            
            if use_brute_force:
                selector = make_selector({"names":[(None, [("select", "{0}.select_get()", "{0}.select_set({1})")])]})
                selector(context.scene.objects, data=data)
            else:
                selector = make_selector({"names":[(None, [("select", "{0}.select_get()", "{0}.select_set({1})")])], "use_kv":True})
                selector(data)
        elif mode == 'EDIT_MESH':
            if select_all_action:
                bpy.ops.mesh.select_all(action=select_all_action)
            
            mesh = active_obj.data
            if is_actual_mode:
                bm = self.bmesh or bmesh.from_edit_mesh(mesh)
                self.bmesh = bm
                faces, edges, verts = bm.faces, bm.edges, bm.verts
            else:
                faces, edges, verts = mesh.polygons, mesh.edges, mesh.vertices
            
            if use_brute_force:
                selector = make_selector({"names":[(None, ["select"])]})
                selector(faces, data=data)
                selector(edges, data=data)
                selector(verts, data=data)
            else:
                selector = make_selector({"names":[(None, ["select"])], "use_kv":True})
                selector(data)
            
            if is_actual_mode:
                #bm.select_flush(True) # ?
                #bm.select_flush(False) # ?
                #bm.select_flush_mode() # ?
                pass
        elif mode in {'EDIT_CURVE', 'EDIT_SURFACE'}:
            if select_all_action:
                bpy.ops.curve.select_all(action=select_all_action)
            
            bezier_names = (bpy.types.BezierSplinePoint, ["select_control_point", "select_left_handle", "select_right_handle"])
            if use_brute_force:
                selector = make_selector({"names":[bezier_names]}, {"names":[(None, ["select"])]})
                for spline in active_obj.data.splines:
                    selector(spline.bezier_points, spline.points, data=data)
            else:
                selector = make_selector({"names":[bezier_names, (None, ["select"])], "use_kv":True})
                selector(data)
        elif mode == 'EDIT_METABALL':
            if select_all_action:
                bpy.ops.mball.select_all(action=select_all_action)
            # Otherwise, we can't do anything with current API
        elif mode == 'EDIT_LATTICE':
            if select_all_action:
                bpy.ops.lattice.select_all(action=select_all_action)
            
            if use_brute_force:
                selector = make_selector({"names":[(None, ["select"])]})
                selector(active_obj.data.points, data=data)
            else:
                selector = make_selector({"names":[(None, ["select"])], "use_kv":True})
                selector(data)
        elif mode == 'EDIT_ARMATURE':
            if select_all_action:
                bpy.ops.armature.select_all(action=select_all_action)
            
            if use_brute_force:
                selector = make_selector({"names":[(None, ["select_head", "select", "select_tail"])]})
                selector(active_obj.data.edit_bones, data=data)
            else:
                selector = make_selector({"names":[(None, ["select_head", "select", "select_tail"])], "use_kv":True})
                selector(data)
        elif mode == 'POSE':
            if select_all_action:
                bpy.ops.pose.select_all(action=select_all_action)
            
            if use_brute_force:
                selector = make_selector({"names":[(None, ["select"])], "item_map":"context.data.bones"})
                selector(active_obj.data.bones, data=data, context=active_obj)
            else:
                selector = make_selector({"names":[(None, ["select"])], "item_map":"context.data.bones", "use_kv":True})
                selector(data, context=active_obj)
        elif mode == 'PARTICLE':
            if select_all_action:
                bpy.ops.particle.select_all(action=select_all_action)
            # Theoretically, particle keys can be selected,
            # but there seems to be no API for working with this
        else:
            pass # no selectable elements in other modes

class SelectionSnapshot:
    # The goal of SelectionSnapshot is to leave as little side-effects as possible,
    # so brute_force_update=True (since select_all operators are recorded in the info log)
    def __init__(self, context=None, brute_force_update=True):
        sel = Selection(context, brute_force_update=brute_force_update)
        self.snapshot_curr = (sel, sel.active, sel.history, sel.selected)
        
        self.mode = sel.normalized_mode
        if self.mode == 'OBJECT':
            self.snapshot_obj = self.snapshot_curr
        else:
            sel = Selection(context, 'OBJECT', brute_force_update=brute_force_update)
            self.snapshot_obj = (sel, sel.active, sel.history, sel.selected)
    
    # Attention: it is assumed that there was no Undo,
    # objects' modes didn't change, and all elements are still valid
    def restore(self):
        if self.mode != 'OBJECT':
            sel, active, history, selected = self.snapshot_obj
            sel.selected = selected
            sel.history = history
            sel.active = active
        
        sel, active, history, selected = self.snapshot_curr
        sel.selected = selected
        sel.history = history
        sel.active = active
    
    def __str__(self):
        if self.mode != 'OBJECT':
            return str({'OBJECT':self.snapshot_obj[1:], self.mode:self.snapshot_curr[1:]})
        else:
            return str({'OBJECT':self.snapshot_obj[1:]})
    
    def __enter__(self):
        pass
    
    def __exit__(self, type, value, traceback):
        self.restore()

def IndividuallyActiveSelected(objects, context=None, make_visble=False):
    if context is None: context = bpy.context
    
    prev_selection = SelectionSnapshot(context)
    sel, active, history, selected = prev_selection.snapshot_obj
    
    sel.selected = {}
    
    view_layer = context.view_layer
    view_layer_objects = view_layer.objects
    
    for obj in objects:
        try:
            view_layer_objects.active = obj
            obj.select_set(True, view_layer=view_layer)
        except Exception as exc:
            continue # object doesn't exist or not in view_layer
        
        if make_visble:
            prev_hide = obj.hide_get(view_layer=view_layer)
            prev_hide_viewport = obj.hide_viewport
            obj.hide = False
            obj.hide_viewport = False
        
        yield obj
        
        if make_visble:
            obj.hide_set(prev_hide, view_layer=view_layer)
            obj.hide_viewport = prev_hide_viewport
        
        obj.select_set(False, view_layer=view_layer)
    
    prev_selection.restore()

class ResumableSelection:
    def __init__(self, *args, **kwargs):
        kwargs["copy_bmesh"] = True # seems like this is REQUIRED to avoid crashes
        self.selection = Selection(*args, **kwargs)
        self.selection_walker = None
        self.selection_initialized = False
        self.selection_total = 0
        self.selection_count = 0
        
        # Screen change doesn't actually invalidate the selection,
        # but it's a big enough change to justify the extra wait.
        # I added it to make batch-transform a bit more efficient.
        self.mode = None
        self.obj_hash = None
        self.screen_hash = None
        self.scene_hash = None
        self.undo_hash = None
        self.operators_len = None
        self.objects_len = None
        self.objects_selected_len = None
    
    def __call__(self, duration=0):
        if duration is None: duration = float("inf")
        context = bpy.context
        wm = context.window_manager
        screen = context.screen
        scene = context.scene
        active_obj = context.object
        mode = context.mode
        obj_hash = (active_obj.as_pointer() if active_obj else 0)
        screen_hash = screen.as_pointer()
        scene_hash = scene.as_pointer()
        undo_hash = bpy.data.as_pointer()
        operators_len = len(wm.operators)
        objects_len = len(scene.objects)
        objects_selected_len = len(context.selected_objects)
        
        object_updated = False
        if active_obj and ('EDIT' in active_obj.mode):
            object_updated |= (active_obj.is_updated or active_obj.is_updated_data)
            data = active_obj.data
            if data: object_updated |= (data.is_updated or data.is_updated_data)
            
            # ATTENTION: inside mesh editmode, undo/redo DOES NOT affect
            # the rest of the blender objects, so pointers/hashes don't change.
            if mode == 'EDIT_MESH':
                bm = self.selection.bmesh
                object_updated |= (bm is None) or (not bm.is_valid)
        
        reset = object_updated
        reset |= (self.mode != mode)
        reset |= (self.obj_hash != obj_hash)
        reset |= (self.screen_hash != screen_hash)
        reset |= (self.scene_hash != scene_hash)
        reset |= (self.undo_hash != undo_hash)
        reset |= (self.operators_len != operators_len)
        #reset |= (self.objects_len != objects_len)
        reset |= (self.objects_selected_len != objects_selected_len)
        if reset:
            self.mode = mode
            self.obj_hash = obj_hash
            self.screen_hash = screen_hash
            self.scene_hash = scene_hash
            self.undo_hash = undo_hash
            self.operators_len = operators_len
            self.objects_len = objects_len
            self.objects_selected_len = objects_selected_len
            
            self.selection.bmesh = None
            self.selection_walker = None
        
        clock = time.perf_counter
        time_stop = clock() + duration
        
        if self.selection_walker is None:
            self.selection.bmesh = None
            self.selection_walker = self.selection.walk()
            self.selection_initialized = False
            self.selection_total = 0
            self.selection_count = 0
            yield (-2, None) # RESET
            if clock() > time_stop: return
        
        if not self.selection_initialized:
            item = next(self.selection_walker, None)
            if item: # can be None if active mode does not support selections
                history, active, total = item
                if mode == 'EDIT_MESH': active = (history[-1] if history else None)
                self.selection_initialized = True
                self.selection_total = total
                yield (0, active) # ACTIVE
                if clock() > time_stop: return
        
        for item in self.selection_walker:
            self.selection_count += 1
            if item[1]: yield (1, item[0]) # SELECTED
            if clock() > time_stop: break
        else: # the iterator is exhausted
            self.selection.bmesh = None
            self.selection_walker = None
            yield (-1, None) # FINISHED
    
    RESET = -2
    FINISHED = -1
    ACTIVE = 0
    SELECTED = 1
