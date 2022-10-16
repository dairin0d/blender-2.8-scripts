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

import bpy

import bmesh

import math
import time
import os
import re
import itertools
import collections

import mathutils
from mathutils import Color, Vector, Euler, Quaternion, Matrix
from mathutils.geometry import intersect_line_sphere

from .utils_math import matrix_compose, matrix_inverted_safe, transform_point_normal
from .bounds import Bounds, RangeAggregator
from .utils_python import attrs_to_dict
from .bpy_inspect import BlRna

# =========================================================================== #

class UndoBlock:
    def __init__(self, message):
        self.message = message
    def __enter__(self):
        pass
    def __exit__(self, exc_type, exc_value, exc_traceback):
        if not exc_type: bpy.ops.ed.undo_push(message=self.message)

# =========================================================================== #

def get_idblock(bpy_data, idname):
    # bpy_data.get() doesn't find linked blocks by their full name
    idblock = bpy_data.get(idname)
    if idblock: return idblock
    for idblock in bpy_data:
        if idblock.name_full == idname: return idblock
    return None

def obj_in_collection(obj, collection, all):
    # Blender does not support searching in collections by full name yet
    # if obj.name is in collection, we still may have name collisions,
    # so we have to check by actual object reference
    objs = (collection.all_objects if all else collection.objects)
    coll_obj = objs.get(obj.name)
    if coll_obj and (coll_obj == obj): return True
    for coll_obj in objs:
        if coll_obj == obj: return True
    return False

# =========================================================================== #

def convert_selection_to_mesh():
    # Scene is expected to be in OBJECT mode
    try:
        bpy.ops.object.convert(target='MESH')
    except Exception as exc:
        active_obj = bpy.context.object
        selected_objs = bpy.context.selected_objects
        print((exc, active_obj, selected_objs))

def bake_location_rotation_scale(location=False, rotation=False, scale=False):
    if not (location or rotation or scale): return
    try:
        bpy.ops.object.transform_apply(location=location, rotation=rotation, scale=scale)
    except Exception as exc:
        active_obj = bpy.context.object
        selected_objs = bpy.context.selected_objects
        print((exc, active_obj, selected_objs))

# see http://www.elysiun.com/forum/showthread.php?304199-Apply-Shape-Keys-in-2-68
def apply_shapekeys(obj):
    if not hasattr(obj.data, "shape_keys"): return
    if not obj.data.shape_keys: return
    if len(obj.data.shape_keys.key_blocks) == 0: return
    
    if obj.data.users > 1: obj.data = obj.data.copy() # don't affect other objects
    
    # bake current shape in a new key (and remove it last)
    shape_key = obj.shape_key_add(name="Key", from_mix=True)
    shape_key.value = 1.0 # important
    
    # DON'T use "all=True" option, it removes in the wrong order
    n_keys = len(obj.data.shape_keys.key_blocks)
    for i in range(n_keys):
        # remove base key, then next one will become base, an so on
        obj.active_shape_key_index = 0
        # This seems to be the only way to remove a shape key
        bpy.ops.object.shape_key_remove(all=False)

def apply_modifier(name, apply_as='DATA', keep_modifier=False, which='DEFAULT'):
    try:
        if which == 'GPENCIL':
            bpy.ops.object.gpencil_modifier_apply(modifier=name, apply_as=apply_as)
        else:
            # In Blender 2.90, the apply_as argument was removed from
            # modifier_apply(), and modifier_apply_as_shapekey() was added
            if bpy.app.version < (2, 90, 0):
                bpy.ops.object.modifier_apply(modifier=name, apply_as=apply_as)
            else:
                if apply_as == 'SHAPE':
                    bpy.ops.object.modifier_apply_as_shapekey(modifier=name, keep_modifier=keep_modifier)
                else:
                    bpy.ops.object.modifier_apply(modifier=name)
        
        return 'APPLIED'
    except RuntimeError as exc:
        #print(repr(exc))
        exc_msg = exc.args[0].lower()
        # "Error: Modifier is disabled, skipping apply"
        is_disabled = ("disab" in exc_msg) or ("skip" in exc_msg)
        return ('DISABLED' if is_disabled else 'FAILED')

def apply_constraint(name, owner='OBJECT', mode='DELETE'):
    if mode == 'DISABLE':
        # Based on CONSTRAINT_OT_disable_keep_transform (we can't use it directly, since it relies on UI context)
        # https://developer.blender.org/diffusion/B/browse/master/release/scripts/startup/bl_operators/constraint.py
        obj = bpy.context.object
        if not obj: return 'FAILED'
        
        if owner == 'BONE':
            if obj.type != 'ARMATURE': return 'FAILED'
            
            # active_bone is available in more contexts than active_pose_bone
            active_bone = getattr(bpy.context, "active_bone", None)
            if not active_bone: return 'FAILED'
            
            pose_bone = obj.pose.bones[active_bone.name]
            constraint = pose_bone.constraints.get(name)
            if not constraint: return 'FAILED'
            if constraint.influence == 0.0: return 'DISABLED'
            
            mat = obj.matrix_world @ pose_bone.matrix
            constraint.influence = 0.0
            pose_bone.matrix = obj.matrix_world.inverted() @ mat
        else:
            constraint = obj.constraints.get(name)
            if not constraint: return 'FAILED'
            if constraint.influence == 0.0: return 'DISABLED'
            
            mat = obj.matrix_world
            constraint.influence = 0.0
            obj.matrix_world = mat
        
        return 'APPLIED'
    
    try:
        bpy.ops.constraint.apply(constraint=name, owner=owner)
        return 'APPLIED'
    except RuntimeError as exc:
        #print(repr(exc))
        exc_msg = exc.args[0].lower()
        # "Error: Modifier is disabled, skipping apply"
        is_disabled = ("disab" in exc_msg) or ("skip" in exc_msg)
        return ('DISABLED' if is_disabled else 'FAILED')

def _apply_modifiers(obj, predicate, options=(), apply_as='DATA', which='DEFAULT'):
    covert_to_mesh = ('CONVERT_TO_MESH' in options)
    make_single_user = ('MAKE_SINGLE_USER' in options)
    remove_disabled = ('REMOVE_DISABLED' in options)
    delete_operands = ('DELETE_OPERANDS' in options)
    apply_shape_keys = ('APPLY_SHAPE_KEYS' in options)
    visible_only = ('VISIBLE_ONLY' in options)
    
    objects_to_delete = set()
    
    if which == 'GPENCIL':
        modifiers = obj.grease_pencil_modifiers
        covert_to_mesh = False
        delete_operands = False
        apply_shape_keys = False
    else:
        modifiers = obj.modifiers
    
    # Users will probably want shape keys to be applied regardless of whether there are modifiers
    if apply_shape_keys: apply_shapekeys(obj) # also makes single-user
    
    if not modifiers: return objects_to_delete
    
    if (obj.type != 'MESH') and covert_to_mesh:
        # "Error: Cannot apply constructive modifiers on curve"
        if obj.data.users > 1: obj.data = obj.data.copy() # don't affect other objects
        convert_selection_to_mesh()
    elif make_single_user:
        # "Error: Modifiers cannot be applied to multi-user data"
        if obj.data.users > 1: obj.data = obj.data.copy() # don't affect other objects
    
    for md in tuple(modifiers):
        if not predicate(md): continue
        
        obj_to_delete = None
        if delete_operands and (md.type == 'BOOLEAN'):
            obj_to_delete = md.object
        
        successfully_applied = False
        is_disabled = False
        
        if visible_only and not md.show_viewport:
            is_disabled = True
        else:
            apply_result = apply_modifier(md.name, apply_as, which=which)
            successfully_applied = (apply_result == 'APPLIED')
            is_disabled = (apply_result == 'DISABLED')
        
        if is_disabled and remove_disabled:
            modifiers.remove(md)
        
        if successfully_applied and obj_to_delete:
            objects_to_delete.add(obj_to_delete)
    
    return objects_to_delete

def _apply_constraints(obj, predicate, owner='OBJECT', mode='DELETE', bones='ALL'):
    if owner == 'BONE':
        if obj.type != 'ARMATURE': return
        if obj.mode == 'EDIT': return
        
        obj_bones = obj.data.bones
        pose_bones = obj.pose.bones
        
        if (bones is None) or (bones == 'ALL'):
            bones = obj_bones
        elif bones == 'SELECTED':
            bones = [bone for bone in obj_bones if bone.select]
        
        def activate_bone(bone):
            # At least for now, Blender doesn't actually update context's active bone
            # until bone.select is assigned something (even if it's the same value)
            obj_bones.active = bone
            bone.select = bone.select
        
        active_bone = obj_bones.active
        
        for bone in bones:
            bone_name = (bone if isinstance(bone, str) else bone.name)
            bone = obj_bones.get(bone_name)
            if not bone: continue
            
            activate_bone(bone)
            for constraint in tuple(pose_bones[bone.name].constraints):
                if not predicate(constraint): continue
                apply_constraint(constraint.name, owner=owner, mode=mode)
        
        activate_bone(active_bone)
    else:
        for constraint in tuple(obj.constraints):
            if not predicate(constraint): continue
            apply_constraint(constraint.name, owner=owner, mode=mode)

def _apply_common(context, objects, idnames, obj_mode, apply_func):
    if not objects: return
    
    scene_objs = context.scene.collection.objects
    layer_objs = context.view_layer.objects
    active_obj = layer_objs.active
    selection_prev = tuple(context.selected_objects)
    
    prev_obj_mode = (active_obj.mode if active_obj else 'OBJECT')
    
    if idnames is not None:
        if callable(idnames):
            predicate = idnames
        else:
            predicate = (lambda md: md.type in idnames)
    else:
        predicate = (lambda md: True)
    
    objects_to_delete = set()
    
    # NOTE: without an active object, mode is OBJECT anyway,
    # which is what we need for bpy.ops.object.select_all()
    layer_objs.active = None
    
    if prev_obj_mode == 'OBJECT':
        bpy.ops.object.select_all(action='DESELECT')
    
    scene_objs_names = {obj.name_full for obj in scene_objs}
    
    # NOTE: bpy.ops.object.mode_set() requires an active object
    # that is not linked from a library and not hidden in viewport.
    # Otherwise, poll() will fail, resulting in an exception.
    # However, apparently even all that isn't awlays enough :(
    
    for obj in objects:
        if obj.library: continue
        
        in_scene = obj.name_full in scene_objs_names
        if not in_scene: scene_objs.link(obj)
        
        hide_select = obj.hide_select
        hide_viewport = obj.hide_viewport
        
        obj.hide_select = False
        obj.hide_viewport = False
        layer_objs.active = obj
        
        BlUtil.Object.select_set(obj, True)
        
        if obj_mode and (obj.mode != obj_mode) and bpy.ops.object.mode_set.poll():
            bpy.ops.object.mode_set(mode=obj_mode)
        
        if (not obj_mode) or (obj.mode == obj_mode):
            objects_to_delete.update(apply_func(obj, predicate) or ())
        
        BlUtil.Object.select_set(obj, False)
        if not in_scene: scene_objs.unlink(obj)
        
        obj.hide_select = hide_select
        obj.hide_viewport = hide_viewport
    
    deleted_names = set(obj.name for obj in objects_to_delete)
    
    if (context.mode != prev_obj_mode) and bpy.ops.object.mode_set.poll():
        bpy.ops.object.mode_set(mode=prev_obj_mode)
    
    # Some bpy.ops.object.* operators' poll() may fail in POSE mode
    if context.mode == 'OBJECT':
        if deleted_names:
            for obj in objects_to_delete:
                BlUtil.Object.select_set(obj, True)
            bpy.ops.object.delete(confirm=False)
        
        bpy.ops.object.select_all(action='DESELECT')
        for obj in selection_prev:
            if obj.name in deleted_names: continue
            BlUtil.Object.select_set(obj, True)
    
    if active_obj and (active_obj.name in deleted_names): active_obj = None
    layer_objs.active = active_obj
    
    return objects_to_delete

def apply_modifiers(context, objects, idnames, options=(), apply_as='DATA', which='DEFAULT'):
    def apply_func(obj, predicate):
        return _apply_modifiers(obj, predicate, options, apply_as, which=which)
    
    return _apply_common(context, objects, idnames, 'OBJECT', apply_func)

def apply_constraints(context, objects, idnames, owner='OBJECT', mode='DELETE', bones='ALL'):
    def apply_func(obj, predicate):
        return _apply_constraints(obj, predicate, owner=owner, mode=mode, bones=bones)
    
    obj_mode = ('POSE' if owner == 'BONE' else None)
    return _apply_common(context, objects, idnames, obj_mode, apply_func)

# =========================================================================== #

class Raycaster:
    Result = collections.namedtuple("RaycastResult", ["success", "location", "normal", "index", "object", "collider", "matrix"])
    
    def __init__(self, objs=None, depsgraph=None, exclude=None, matrix=None):
        self.matrix = matrix
        self.colliders = []
        self.add_colliders(objs, depsgraph, exclude)
    
    def __set_matrix(self, matrix, inv):
        if matrix:
            matrix = Matrix(matrix)
            matrix_inv = matrix.inverted_safe()
            if inv:
                self.__matrix, self.__matrix_inv = matrix_inv, matrix
            else:
                self.__matrix, self.__matrix_inv = matrix, matrix_inv
        else:
            self.__matrix = None
            self.__matrix_inv = None
    
    @property
    def matrix(self):
        return self.__matrix
    @matrix.setter
    def matrix(self, value):
        self.__set_matrix(value, False)
    
    @property
    def matrix_inv(self):
        return self.__matrix_inv
    @matrix_inv.setter
    def matrix_inv(self, value):
        self.__set_matrix(value, True)
    
    def add_colliders(self, objs=None, depsgraph=None, exclude=None):
        if (not objs) and (objs is not None): return
        if exclude is None: exclude = ()
        for obj, obj_eval, matrix in BlUtil.Depsgraph.evaluated_objects(objs, depsgraph=depsgraph):
            if obj in exclude: continue
            self.add_collider(obj, obj_eval, matrix)
    
    def add_collider(self, obj, obj_eval=None, matrix=None, depsgraph=None):
        if not obj_eval:
            if depsgraph is None: depsgraph = bpy.context.evaluated_depsgraph_get()
            obj_eval = obj.evaluated_get(depsgraph)
        
        if obj_eval.type != 'MESH': return
        
        if matrix is None: matrix = obj_eval.matrix_world
        
        matrix = matrix.copy() # make sure to copy
        matrix_inv = matrix.inverted_safe()
        matrix_inv_3x3 = matrix_inv.to_3x3()
        self.colliders.append((obj, obj_eval, matrix, matrix_inv, matrix_inv_3x3))
    
    def closest_point(self, origin, distance=1.84467e+19):
        if self.matrix:
            origin = self.matrix @ origin
        
        best_dist = math.inf
        best_result = None
        
        for obj, collider, matrix, matrix_inv, matrix_inv_3x3 in self.colliders:
            origin_local = (matrix_inv @ origin)
            result = collider.closest_point_on_mesh(origin_local, distance=distance)
            if not result[0]: continue
            
            dist = (origin - (matrix @ result[1])).magnitude
            if dist < best_dist:
                best_result = (*result, obj, collider, matrix)
                best_dist = dist
        
        if best_result:
            success, location, normal, index, obj, collider, matrix = best_result
            
            location, normal = transform_point_normal(matrix, location, normal)
            
            if self.matrix_inv:
                matrix = self.matrix_inv @ matrix
                location, normal = transform_point_normal(self.matrix_inv, location, normal)
            
            return self.Result(success, location, normal, index, obj, collider, matrix)
        
        return self.Result(False, Vector(), Vector(), -1, None, None, Matrix())
    
    def ray_cast(self, ray_start, ray_end):
        if self.matrix:
            ray_start = self.matrix @ ray_start
            ray_end = self.matrix @ ray_end
        ray_dir = ray_end - ray_start
        
        best_z = math.inf
        best_result = None
        
        for obj, collider, matrix, matrix_inv, matrix_inv_3x3 in self.colliders:
            origin, direction = (matrix_inv @ ray_start), (matrix_inv_3x3 @ ray_dir)
            result = collider.ray_cast(origin, direction, distance=direction.magnitude)
            if not result[0]: continue
            
            z_dist = ray_dir.dot(matrix @ result[1])
            if z_dist < best_z:
                best_result = (*result, obj, collider, matrix)
                best_z = z_dist
        
        if best_result:
            success, location, normal, index, obj, collider, matrix = best_result
            
            location, normal = transform_point_normal(matrix, location, normal)
            
            if ray_dir.dot(normal) > 0: normal = -normal # e.g. for objects with negative scale
            
            if self.matrix_inv:
                matrix = self.matrix_inv @ matrix
                location, normal = transform_point_normal(self.matrix_inv, location, normal)
            
            return self.Result(success, location, normal, index, obj, collider, matrix)
        
        return self.Result(False, Vector(), Vector(), -1, None, None, Matrix())

# =========================================================================== #

class BlUtil:
    class Object:
        @staticmethod
        def line_cast(obj, start, end, depsgraph=None, world=False):
            # returns (result, location, normal, index)
            delta = end - start
            return BlUtil.Object.ray_cast(obj, start, delta, delta.magnitude, depsgraph, world)
        
        @staticmethod
        def ray_cast(obj, origin, direction, distance=1.70141e+38, depsgraph=None, world=False):
            if obj.type != 'MESH': return (False, Vector(), Vector(), -1)
            
            if world:
                mi = matrix_inverted_safe(obj.matrix_world)
                origin, direction = (mi @ origin), (mi.to_3x3() @ direction)
            
            # returns (result, location, normal, index)
            result = obj.ray_cast(origin, direction, distance=distance, depsgraph=depsgraph)
            
            if world:
                success, location, normal, index = result
                location, normal = transform_point_normal(obj.matrix_world, location, normal)
                result = success, location, normal, index
            
            return result
        
        @staticmethod
        def rotation_convert(src_mode, q, aa, e, dst_mode, always4=False):
            if src_mode == dst_mode: # and coordsystem is 'BASIS'
                if src_mode == 'QUATERNION':
                    R = Quaternion(q)
                elif src_mode == 'AXIS_ANGLE':
                    R = Vector(aa)
                else:
                    R = Euler(e)
            else:
                if src_mode == 'QUATERNION':
                    R = Quaternion(q)
                elif src_mode == 'AXIS_ANGLE':
                    R = Quaternion(aa[1:], aa[0])
                else:
                    R = Euler(e).to_quaternion()
                
                if dst_mode == 'QUATERNION':
                    pass # already quaternion
                elif dst_mode == 'AXIS_ANGLE':
                    R = R.to_axis_angle()
                    R = Vector((R[1], R[0].x, R[0].y, R[0].z))
                else:
                    R = R.to_euler(dst_mode)
            
            if always4:
                if len(R) == 4: R = Vector(R)
                else: R = Vector((0.0, R[0], R[1], R[2]))
            
            return R
        
        @staticmethod
        def rotation_apply(obj, R, mode):
            if (len(R) == 4) and (mode not in ('QUATERNION', 'AXIS_ANGLE')): R = R[1:]
            
            if obj.rotation_mode == mode: # and coordsystem is 'BASIS'
                if mode == 'QUATERNION':
                    obj.rotation_quaternion = Quaternion(R)
                elif mode == 'AXIS_ANGLE':
                    obj.rotation_axis_angle = tuple(R)
                else:
                    obj.rotation_euler = Euler(R)
            else:
                if mode == 'QUATERNION':
                    R = Quaternion(R)
                elif mode == 'AXIS_ANGLE':
                    R = Quaternion(R[1:], R[0])
                else:
                    R = Euler(R).to_quaternion()
                R.normalize()
                
                if obj.rotation_mode == 'QUATERNION':
                    obj.rotation_quaternion = R
                elif obj.rotation_mode == 'AXIS_ANGLE':
                    R = R.to_axis_angle()
                    R = Vector((R[1], R[0].x, R[0].y, R[0].z))
                    obj.rotation_axis_angle = R
                else:
                    R = R.to_euler(obj.rotation_mode)
                    obj.rotation_euler = R
        
        @staticmethod
        def vertex_location(obj, i, undeformed=False):
            if (not obj) or (i < 0): return Vector()
            if obj.type == 'MESH':
                if (i >= len(obj.data.vertices)): return Vector()
                v = obj.data.vertices[i]
                return Vector(v.undeformed_co if undeformed else v.co)
            elif obj.type in ('CURVE', 'SURFACE'):
                for spline in obj.data.splines:
                    points = (spline.bezier_points if spline.type == 'BEZIER' else spline.points)
                    if i >= len(points):
                        i -= len(points)
                    else:
                        return Vector(points[i].co)
                return Vector()
            elif obj.type == 'LATTICE':
                if (i >= len(obj.data.points)): return Vector()
                p = obj.data.points[i]
                return Vector(v.co if undeformed else v.co_deform)
            else:
                return Vector()
        
        @staticmethod
        def matrix_world(obj, bone_name=""):
            if not obj: return Matrix()
            if bone_name:
                obj = obj.id_data
                if obj.type == 'ARMATURE':
                    bone = obj.pose.bones.get(bone_name)
                    if bone: return obj.matrix_world @ bone.matrix
                return obj.matrix_world
            elif hasattr(obj, "matrix_world"):
                return Matrix(obj.matrix_world)
            else:
                bone = obj
                obj = bone.id_data
                return obj.matrix_world @ bone.matrix
        
        @staticmethod
        def matrix_world_set(obj, m):
            if not obj: return
            if hasattr(obj, "matrix_world"):
                obj.matrix_world = Matrix(m)
            else:
                bone = obj
                obj = bone.id_data
                bone.matrix = matrix_inverted_safe(obj.matrix_world) @ m
        
        @staticmethod
        def matrix_parent(obj):
            if not obj: return Matrix()
            parent = obj.parent
            if not parent: return Matrix()
            
            parent_type = getattr(obj, "parent_type", 'OBJECT')
            if parent_type == 'BONE': # NOT TESTED
                parent_bone = parent.pose.bones.get(obj.parent_bone)
                if not parent_bone: return BlUtil.Object.matrix_world(parent)
                return BlUtil.Object.matrix_world(parent_bone)
            elif parent_type == 'VERTEX': # NOT TESTED
                v = BlUtil.Object.vertex_location(parent, obj.parent_vertices[0])
                return Matrix.Translation(BlUtil.Object.matrix_world(parent) @ v)
            elif parent_type == 'VERTEX_3': # NOT TESTED
                pm = BlUtil.Object.matrix_world(parent)
                v0 = pm @ BlUtil.Object.vertex_location(parent, obj.parent_vertices[0])
                v1 = pm @ BlUtil.Object.vertex_location(parent, obj.parent_vertices[1])
                v2 = pm @ BlUtil.Object.vertex_location(parent, obj.parent_vertices[2])
                t = v0
                x = (v1 - v0).normalized()
                y = (v2 - v0).normalized()
                z = x.cross(y).normalized()
                return matrix_compose(x, y, z, t)
            else:
                # I don't know how CURVE and KEY are supposed to behave,
                # so for now I just treat them the same as OBJECT/ARMATURE.
                # LATTICE isn't a rigid-body/affine transformation either.
                return BlUtil.Object.matrix_world(parent)
        
        @staticmethod
        def root(obj):
            if not obj: return None
            while obj.parent:
                obj = obj.parent
            return obj
        
        @staticmethod
        def local_roots(objs):
            objs = set(objs)
            roots = set()
            obj_parents = BlUtil.Object.parents
            objs_isdisjoint = objs.isdisjoint
            for obj in objs:
                if objs_isdisjoint(obj_parents(obj)): roots.add(obj)
            return roots
        
        @staticmethod
        def parents(obj, include_this=False):
            if not obj: return
            if include_this: yield obj
            parent = obj.parent
            while parent:
                yield parent
                parent = parent.parent
            yield None
        
        @staticmethod
        def map_children(objs=None):
            if objs is None: objs = bpy.data.objects
            objs = set(objs)
            child_map = {}
            parent_map = {}
            obj_parents = BlUtil.Object.parents
            child_map_setdefault = child_map.setdefault
            for obj in objs:
                parent = None
                for parent in obj_parents(obj):
                    if parent in objs: break
                child_map_setdefault(parent, []).append(obj)
                child_map_setdefault(obj, [])
                parent_map[obj] = parent
            return child_map, parent_map
        
        @staticmethod
        def all_children(obj, result=None, child_map=None, filter=None):
            if child_map is None: child_map = BlUtil.Object.map_children()[0]
            if result is None: result = []
            children = child_map.get(obj)
            if children:
                add = (result.append if isinstance(result, list) else result.add)
                add_children = BlUtil.Object.all_children
                for child in children:
                    if filter and not filter(child): continue
                    add(child)
                    add_children(child, result, child_map)
            return result
        
        @staticmethod
        def iter_bone_info(obj):
            data = obj.data
            if obj.mode == 'EDIT':
                for bone in data.edit_bones:
                    yield (bone, (bone.select, bone.select_head, bone.select_tail))
            else:
                for bone in obj.pose.bones:
                    _bone = bone.bone #data.bones[bone.name] # equivalent
                    yield (bone, (_bone.select, _bone.select_head, _bone.select_tail))
        
        @staticmethod
        def bounding_box(obj, matrix=None):
            if (obj.type == 'LATTICE') and (not obj.is_evaluated):
                # In Blender 2.8, original (non-"evaluated") lattices always
                # return (-0.5, -0.5, -0.5), (0.5, 0.5, 0.5) as their bounding box
                
                # For some reason, LatticePoint.co returns weird
                # values, while LatticePoint.co_deform behaves ok
                points = (p.co_deform for p in obj.data.points)
                bounds = Bounds.MinMax(*RangeAggregator(3)(points))
            else:
                bounds = Bounds.MinMax(obj.bound_box[0], obj.bound_box[-2])
            
            if matrix: bounds.transform(matrix)
            
            return bounds
        
        @staticmethod
        def get_geometry(obj, source, mode, verts, edges, faces):
            # source: depsgraph, 'EDIT', (raw)
            # mode: geometry, bbox, pivot
            # verts: all, only belonging to faces/edges
            # edges: all, none
            # faces: tris, polys, none
            raise NotImplementedError("BlUtil.Object.get_geometry() is not implemented")
        
        # active/selection/hidden states can be different for each view layer
        
        @staticmethod
        def select_get(obj, view_layer=None):
            try:
                return obj.select_get(view_layer=view_layer)
            except RuntimeError: # happens when obj is not in view layer
                return False
        
        @staticmethod
        def select_set(obj, value, view_layer=None):
            try:
                obj.select_set(value, view_layer=view_layer)
            except RuntimeError: # happens when obj is not in view layer
                pass
        
        @staticmethod
        def visible_get(obj, view_layer=None, viewport=None):
            try:
                return obj.visible_get(view_layer=view_layer, viewport=viewport)
            except RuntimeError: # happens when obj is not in view layer
                return not obj.hide_viewport
        
        @staticmethod
        def hide_get(obj, view_layer=None):
            try:
                return obj.hide_get(view_layer=view_layer)
            except RuntimeError: # happens when obj is not in view layer
                return obj.hide_viewport
        
        @staticmethod
        def hide_set(obj, value, view_layer=None):
            try:
                obj.hide_set(value, view_layer=view_layer)
            except RuntimeError: # happens when obj is not in view layer
                obj.hide_viewport = value
        
        @staticmethod
        def select_all(action, context=None):
            if not context: context = bpy.context
            
            # object.select_all() operator requires OBJECT mode
            if context.mode == 'OBJECT':
                bpy.ops.object.select_all(action=action)
            else:
                # we don't want to use object.mode_set() because
                # if affects all selected objects; selecting
                # manually allows us to avoid such side-effects
                view_layer = context.view_layer
                
                if action == 'TOGGLE': action = ('DESELECT' if context.selected_objects else 'SELECT')
                
                if action == 'INVERT':
                    for obj in view_layer.objects:
                        value = not obj.select_get(view_layer=view_layer)
                        obj.select_set(value, view_layer=view_layer)
                else:
                    value = (action == 'SELECT')
                    for obj in view_layer.objects:
                        obj.select_set(value, view_layer=view_layer)
        
        @staticmethod
        def active_get(view_layer=None):
            if not view_layer: view_layer = bpy.context.view_layer
            return view_layer.objects.active
        
        @staticmethod
        def active_set(obj, view_layer=None):
            if not view_layer: view_layer = bpy.context.view_layer
            
            try:
                view_layer.objects.active = obj
            except RuntimeError: # happens when obj is not in view layer
                view_layer.objects.active = None
        
        @staticmethod
        def select_activate(objs, mode, active='ANY', view_layer=None):
            if not view_layer: view_layer = bpy.context.view_layer
            
            if objs is None: objs = ()
            elif isinstance(objs, bpy.types.Object): objs = [objs]
            
            layer_objs = set(view_layer.objects)
            objs = [obj for obj in objs if obj in layer_objs]
            
            if mode in ('DESELECT', 'MUTE', 'INVERSE'):
                for obj in objs:
                    obj.select_set(False, view_layer=view_layer)
                
                if active and (view_layer.objects.active in objs):
                    view_layer.objects.active = None
                
                if mode == 'DESELECT': return
                
                layer_objs.difference_update(objs)
                
                for obj in layer_objs:
                    obj.select_set(True, view_layer=view_layer)
                
                if active and (not view_layer.objects.active):
                    view_layer.objects.active = next(iter(layer_objs), None)
            elif mode in ('SELECT', 'SOLO', 'ISOLATE'):
                for obj in objs:
                    obj.select_set(True, view_layer=view_layer)
                
                if active:
                    if (active == 'ANY') and (view_layer.objects.active not in objs):
                        active = 'LAST'
                    
                    if not objs:
                        view_layer.objects.active = None
                    elif active == 'FIRST':
                        view_layer.objects.active = objs[0]
                    elif active == 'LAST':
                        view_layer.objects.active = objs[-1]
                
                if mode == 'SELECT': return
                
                layer_objs.difference_update(objs)
                
                for obj in layer_objs:
                    obj.select_set(False, view_layer=view_layer)
    
    class Data:
        @staticmethod
        def all_iter(non_removable=True, exclude=()):
            bpy_data = bpy.data
            bpy_prop_collection = bpy.types.bpy_prop_collection
            
            for attr_name in dir(bpy_data):
                if attr_name.startswith("_"): continue
                if attr_name in exclude: continue
                attr = getattr(bpy_data, attr_name)
                if not isinstance(attr, bpy_prop_collection): continue
                if not (non_removable or hasattr(attr, "remove")): continue
                yield attr_name, attr
        
        @staticmethod
        def all_map(non_removable=True, exclude=()):
            return {k: v for k, v in BlUtil.Data.all_iter(non_removable, exclude)}
        
        @staticmethod
        def all_list(non_removable=True, exclude=()):
            return [v for k, v in BlUtil.Data.all_iter(non_removable, exclude)]
        
        @staticmethod
        def get_users_map(bpy_datas, use_fake_user=True):
            if hasattr(bpy_datas, "values"): bpy_datas = bpy_datas.values() # dict
            
            users_map = {}
            
            for bpy_data in bpy_datas:
                if isinstance(bpy_data, tuple): bpy_data = bpy_data[1] # all_iter()
                
                for item in bpy_data:
                    users = item.users
                    if not use_fake_user: users -= int(item.use_fake_user)
                    users_map[(bpy_data, item)] = users
            
            return users_map
        
        @staticmethod
        def clear_orphaned(users_map0, users_map1, check='OLD'):
            def remove(users_map1, key):
                bpy_data, item = key
                
                try:
                    bpy_data.remove(item)
                except ReferenceError:
                    pass
                
                users_map1.pop(key, None)
            
            modified = False
            
            if check == 'OLD':
                check_old = True
                check_new = False
            elif check == 'NEW':
                check_old = False
                check_new = True
            else:
                check_old = True
                check_new = True
            
            if check_old:
                # If object had users, but now doesn't, remove it
                for key, users0 in users_map0.items():
                    users1 = users_map1.get(key, 0)
                    if (users1 > 0) or (users0 == users1): continue
                    remove(users_map1, key)
                    modified = True
            
            if check_new:
                # If object has no users and wasn't present before, remove it
                for key, users1 in tuple(users_map1.items()):
                    if (users1 > 0) or (key in users_map0): continue
                    remove(users_map1, key)
                    modified = True
            
            return modified
        
        class OrphanCleanup:
            def __init__(self, exclude=(), use_fake_user=True, check='OLD'):
                self.exclude = exclude
                self.use_fake_user = use_fake_user
                self.check = check
            
            def __enter__(self):
                self.bpy_datas = BlUtil.Data.all_list(False, exclude=self.exclude)
                self.users_map0 = BlUtil.Data.get_users_map(self.bpy_datas, self.use_fake_user)
            
            def __exit__(self, exc_type, exc_value, exc_traceback):
                bpy_datas = self.bpy_datas
                users_map0 = self.users_map0
                users_map1 = BlUtil.Data.get_users_map(bpy_datas, self.use_fake_user)
                
                while BlUtil.Data.clear_orphaned(users_map0, users_map1, self.check):
                    users_map0 = users_map1
                    users_map1 = BlUtil.Data.get_users_map(bpy_datas, self.use_fake_user)
    
    class Bones:
        @staticmethod
        def active_get(obj=None, pose=False):
            if not obj:
                if pose:
                    if hasattr(bpy.context, "active_pose_bone"): return bpy.context.active_pose_bone
                else:
                    if hasattr(bpy.context, "active_bone"): return bpy.context.active_bone
                
                obj = bpy.context.object
            
            if (not obj) or (obj.type != 'ARMATURE'): return None
            
            bone = (obj.data.bones.active if obj.mode != 'EDIT' else obj.data.edit_bones.active)
            
            if pose and bone:
                # In edit mode, edit bones may not correspond to regular or pose bones
                if obj.mode == 'EDIT': return None
                bone = obj.pose.bones[bone.name]
            
            return bone
        
        @staticmethod
        def active_set(obj, bone):
            if not obj: obj = bpy.context.object
            if (not obj) or (obj.type != 'ARMATURE'): return
            
            if not isinstance(bone, str): bone = bone.name
            
            bones = (obj.data.bones if obj.mode != 'EDIT' else obj.data.edit_bones)
            bone = bones.get(bone)
            
            # At least for now, Blender doesn't actually update context's active bone
            # until bone.select is assigned something (even if it's the same value)
            bones.active = bone
            bone.select = bone.select
    
    class Collection:
        @staticmethod
        def map_children(coll, cache=None):
            null_res = (None, None)
            if not coll: return null_res
            child_map, parent_map = (null_res if cache is None else cache.get(coll, null_res))
            if child_map is None:
                child_map, parent_map = BlUtil.Object.map_children(coll.all_objects)
                if cache is not None: cache[coll] = (child_map, parent_map)
            return child_map, parent_map
        
        @staticmethod
        def all_instance_refs(obj, result=None):
            if result is None: result = set()
            if obj.instance_type != 'COLLECTION': return result
            coll = obj.instance_collection
            if not coll: return result
            if coll in result: return result # skip recursive search
            result.add(coll)
            BlUtil.Collection.all_children(coll, result)
            for obj in coll.objects:
                BlUtil.Collection.all_instance_refs(obj, result)
            return result
        
        @staticmethod
        def all_children(coll, result=None):
            if result is None: result = set()
            for child_coll in coll.children:
                result.add(child_coll)
                BlUtil.Collection.all_children(child_coll, result)
            return result
        
        @staticmethod
        def contains(coll, value, recursive=False):
            # Note: as of Blender 2.80, bpy collections support only string key lookup
            if not value: return False
            if isinstance(value, bpy.types.Collection):
                def search(coll):
                    if coll.children.get(value.name) == value: return True
                    for child in coll.children:
                        if child == value: return True
                        if recursive and search(child): return True
                    return False
                return search(coll)
            elif isinstance(value, bpy.types.Object):
                objs = (coll.all_objects if recursive else coll.objects)
                if objs.get(value.name) == value: return True
                for obj in objs:
                    if obj == value: return True
            return False
    
    class Depsgraph:
        @staticmethod
        def trigger_update():
            for window in bpy.context.window_manager.windows:
                window.scene.cursor.location = Vector(window.scene.cursor.location)
        
        @staticmethod
        def evaluated_vertices(depsgraph, objs=None, matrix=None, origins='NON_GEOMETRY'):
            instances = True
            
            if objs is None:
                objs = depsgraph.object_instances
                instances = False
            
            verts = []
            
            add_obj = None
            
            if origins and (origins != 'NONE'):
                # Any object types that can have geometry or sub-elements
                geometry_types = {'MESH', 'CURVE', 'SURFACE', 'FONT', 'META', 'GPENCIL', 'LATTICE', 'ARMATURE'}
                
                def add_obj(obj, result):
                    if (origins == 'NON_GEOMETRY') and (obj.type not in geometry_types):
                        verts.append(obj.matrix_world.to_translation())
            
            MeshEquivalent.gather(objs, depsgraph, matrix=matrix, edit=None, instances=instances, verts=verts, add_obj=add_obj)
            
            return verts
        
        @staticmethod
        def bounding_box(depsgraph, objs=None, matrix=None, origins='NON_GEOMETRY', use_bbox=False):
            if use_bbox:
                geometry_types = {'MESH', 'CURVE', 'SURFACE', 'FONT', 'META', 'GPENCIL', 'LATTICE', 'ARMATURE'}
                aggregator = RangeAggregator(3)
                for obj_main, obj_eval, instance_matrix in BlUtil.Depsgraph.evaluated_objects(objs, depsgraph):
                    if (origins == 'NONE') and (obj_eval.type not in geometry_types): continue
                    if matrix: instance_matrix = matrix @ instance_matrix
                    aggregator(BlUtil.Object.bounding_box(obj_eval, instance_matrix))
                return Bounds.MinMax(*aggregator)
            else:
                points = BlUtil.Depsgraph.evaluated_vertices(depsgraph, objs, matrix, origins)
                return Bounds.MinMax(*RangeAggregator(3)(points))
        
        @staticmethod
        def evaluated_objects(objs=None, depsgraph=None, originals=True, instances=True):
            if depsgraph is None: depsgraph = bpy.context.evaluated_depsgraph_get()
            
            if objs is None: objs = depsgraph.objects
            
            if isinstance(objs, bpy.types.Object):
                objs = {objs}
            elif not isinstance(objs, set):
                objs = set(objs)
            
            if originals:
                for obj in objs:
                    if obj.is_evaluated:
                        yield (obj.original, obj, obj.matrix_world)
                    else:
                        obj_eval = obj.evaluated_get(depsgraph)
                        yield (obj, obj_eval, obj_eval.matrix_world)
            
            for i in depsgraph.object_instances:
                if not i.is_instance: continue
                obj = i.parent.original
                if (obj not in objs) and (i.parent not in objs): continue
                yield (obj, i.instance_object, i.matrix_world)
    
    class Scene:
        @staticmethod
        def line_cast(scene, subset, start, end):
            # returns (result, location, normal, index, object, matrix)
            delta = end - start
            return BlUtil.Scene.ray_cast(scene, subset, start, delta, delta.magnitude)
        
        @staticmethod
        def ray_cast(scene, subset, origin, direction, distance=1.70141e+38):
            # returns (result, location, normal, index, object, matrix)
            if bpy.app.version >= (2, 91, 0):
                # Expects a depsgraph
                if isinstance(subset, bpy.types.ViewLayer):
                    subset = subset.depsgraph
            else:
                # Expects a view layer
                if isinstance(subset, bpy.types.Depsgraph):
                    subset = subset.view_layer_eval
            return scene.ray_cast(subset, origin, direction, distance=distance)
        
        @staticmethod
        def update(target):
            # At some point, Blender moved Scene.update()
            # functionality to ViewLayer.update()
            if hasattr(target, "update"): # view layer or old scene
                target.update()
            elif isinstance(target, bpy.types.ViewLayer): # old view layer
                for scene in bpy.data.scenes:
                    for view_layer in target.view_layers:
                        if view_layer == target:
                            scene.update()
                            return
            elif isinstance(target, bpy.types.Scene):
                for view_layer in target.view_layers:
                    view_layer.update()
    
    class Camera:
        @staticmethod
        def projection_info(cam, scene=None):
            if scene is None: scene = bpy.context.scene
            render = scene.render
            
            w = render.resolution_x * render.pixel_aspect_x
            h = render.resolution_y * render.pixel_aspect_y
            if cam.sensor_fit == 'HORIZONTAL':
                wh_norm = w
                sensor_size = cam.sensor_width
            elif cam.sensor_fit == 'VERTICAL':
                wh_norm = h
                sensor_size = cam.sensor_height
            else:
                wh_norm = max(w, h)
                sensor_size = cam.sensor_width
            w = w / wh_norm
            h = h / wh_norm
            
            if cam.type == 'ORTHO':
                persp = 0.0
                scale = cam.ortho_scale
                sx = w * scale
                sy = h * scale
                dx = cam.shift_x * scale
                dy = cam.shift_y * scale
                dz = scale
            else:
                persp = 1.0
                sx = w
                sy = h
                dx = cam.shift_x
                dy = cam.shift_y
                dz = cam.lens / sensor_size
            
            return (Vector((sx, sy, persp)), Vector((dx, dy, dz)))
    
    class Orientation:
        @staticmethod
        def create(context, name, matrix=None, overwrite=False, normalize=True):
            scene = context.scene
            
            if not overwrite:
                basename = name
                i = 1
                while name in scene.orientations:
                    name = "%s.%03i" % (basename, i)
                    i += 1
            
            bpy.ops.transform.create_orientation(attrs_to_dict(context), name=name, use_view=True, use=False, overwrite=overwrite)
            
            tfm_orient = scene.orientations[-1]
            tfm_orient.name = name
            
            if matrix:
                matrix = matrix.to_3x3()
                if normalize: matrix.normalize()
                tfm_orient.matrix = matrix
            
            return tfm_orient
        
        @staticmethod
        def update(context, name, matrix, auto_create=True, normalize=True):
            scene = context.scene
            tfm_orient = scene.orientations.get(name)
            if tfm_orient:
                matrix = matrix.to_3x3()
                if normalize: matrix.normalize()
                tfm_orient.matrix = matrix
            elif auto_create:
                tfm_orient = BlUtil.Orientation.create(context, name, matrix, normalize=normalize)
    
    class BMesh:
        @staticmethod
        def layer_get(bmlc, elem, default, layer=None):
            if layer is None: layer = bmlc.active
            elif isinstance(layer, str): layer = bmlc.get(layer)
            if layer is None: return default
            return elem[layer]
        
        @staticmethod
        def layer_set(bmlc, elem, value, layer=None):
            if layer is None:
                layer_name = "Active"
                layer = bmlc.active
            elif isinstance(layer, str):
                layer_name = layer
                layer = bmlc.get(layer)
            if layer is None: layer = bmlc.new(layer_name)
            elem[layer] = value
    
    class Meta:
        _numbers = set("0123456789")
        _suffix = ".0123456789"
        
        @staticmethod
        def main_name(name):
            # Note: if Meta object has any numbers in its name AT ALL,
            # Blender will consider it "not main" meta-object
            # (and thus won't generate any geometry for it)
            if BlUtil.Meta._numbers.isdisjoint(name): return name
            return name.rstrip(BlUtil.Meta._suffix)
    
    class Spline:
        @staticmethod
        def find_point(curve, p):
            if isinstance(p, bpy.types.SplinePoint):
                for spline in curve.splines:
                    if spline.type == 'BEZIER': continue
                    for i, wp in enumerate(spline.points):
                        if wp == p: return (spline, i)
            else:
                for spline in curve.splines:
                    if spline.type != 'BEZIER': continue
                    for i, wp in enumerate(spline.bezier_points):
                        if wp == p: return (spline, i)
            return (None, -1)
        
        @staticmethod
        def neighbors(spline, i):
            points = (spline.bezier_points if spline.type == 'BEZIER' else spline.points)
            n = len(points)
            i0 = i-1
            i1 = i+1
            
            if (i0 < 0):
                if spline.use_cyclic_u:
                    p0 = points[n + i0]
                else:
                    p0 = None
            else:
                p0 = points[i0]
            
            if (i1 >= n):
                if spline.use_cyclic_u:
                    p1 = points[i1 - n]
                else:
                    p1 = None
            else:
                p1 = points[i1]
            
            return p0, p1
        
        @staticmethod
        def point_xyztw(p, obj=None):
            if not obj: obj = bpy.context.object # expected to be a Curve
            
            if isinstance(p, bpy.types.SplinePoint):
                co = p.co
                w = co[3]
                t = Vector((co[0], co[1], co[2]))
                return None, None, None, t, w
            else:
                p0, p1 = None, None
                if obj and (obj.type == 'CURVE'):
                    spline, i = BlUtil.Spline.find_point(obj.data, p)
                    if spline:
                        p0, p1 = BlUtil.Spline.neighbors(spline, i)
                
                if p.select_control_point:
                    t = Vector(p.co)
                    z = -(p.handle_right - p.handle_left).normalized()
                elif p.select_left_handle and p.select_right_handle:
                    t = (p.handle_right + p.handle_left) * 0.5
                    z = -(p.handle_right - p.handle_left).normalized()
                elif p.select_left_handle:
                    t = Vector(p.handle_left)
                    z = -(p.co - p.handle_left).normalized()
                elif p.select_right_handle:
                    t = Vector(p.handle_right)
                    z = -(p.handle_right - p.co).normalized()
                
                x_from_handles = (p.handle_left_type in ('VECTOR', 'FREE')) or (p.handle_right_type in ('VECTOR', 'FREE'))
                if x_from_handles:
                    x = -(p.handle_left - p.co).cross(p.handle_right - p.co).normalized()
                    y = -z.cross(x)
                elif (p0 and p1):
                    x = -(p0.co - p.co).cross(p1.co - p.co).normalized()
                    y = -z.cross(x)
                else:
                    x = None
                    y = None
                
                return x, y, z, t, None
    
    class Material:
        @staticmethod
        def copy_nodes(src_mat, dst_mat, include=None, exclude=None):
            # So far, Blender has no API to directly copy nodes between node trees,
            # and BlRna serializing/deserializing can't fully copy some node data
            src_use_nodes = src_mat.use_nodes
            dst_use_nodes = dst_mat.use_nodes
            
            src_mat.use_nodes = True
            dst_mat.use_nodes = True
            
            context = bpy.context
            scene = context.scene
            view_layer = context.view_layer
            
            mesh = bpy.data.meshes.new("CopyNodes")
            mesh.materials.append(src_mat)
            mesh.materials.append(dst_mat)
            obj = bpy.data.objects.new("CopyNodes", mesh)
            scene.collection.objects.link(obj)
            obj.select_set(True)
            
            active_obj = view_layer.objects.active
            view_layer.objects.active = obj
            
            area = context.area
            area_type = area.type
            area.type = 'NODE_EDITOR'
            area_ui_type = area.ui_type
            area.ui_type = 'ShaderNodeTree'
            space = context.space_data
            
            def set_node_tree(node_tree):
                space.pin = False # just to be sure
                space.path.start(node_tree)
            
            if include:
                node_filter = (include if callable(include) else (lambda node: node in include))
            elif exclude:
                node_filter = ((lambda node: not exclude(node)) if callable(exclude) else (lambda node: node not in exclude))
            else:
                node_filter = (lambda node: True)
            
            # It seems that assigning obj.active_material_index AND space.node_tree is necessary
            obj.active_material_index = 0
            set_node_tree(src_mat.node_tree)
            node_select_info = [(node, node.select) for node in src_mat.node_tree.nodes]
            for node in src_mat.node_tree.nodes:
                node.select = node_filter(node)
            bpy.ops.node.clipboard_copy()
            for node, select in node_select_info:
                node.select = select
            
            obj.active_material_index = 1
            set_node_tree(dst_mat.node_tree)
            bpy.ops.node.clipboard_paste()
            
            area.ui_type = area_ui_type
            area.type = area_type
            view_layer.objects.active = active_obj
            
            bpy.data.objects.remove(obj)
            bpy.data.meshes.remove(mesh)
            
            src_mat.use_nodes = src_use_nodes
            dst_mat.use_nodes = dst_use_nodes
    
    class Image:
        # path_mode: 'DONT_CHANGE', 'ABSOLUTE', 'RELATIVE'
        # reuse: 'NONE', 'REUSE', 'RELOAD'
        # sequences: 'NONE', 'NO_GAPS', 'WITH_GAPS'
        # alpha_mode: 'DONT_CHANGE', 'NONE', 'STRAIGHT', 'PREMUL', 'CHANNEL_PACKED'
        # returns image if filepaths is str, otherwise {shared_key: {category_id: (image, index_start, frame_count), ...}, ...}
        @staticmethod
        def load(filepaths, directory="", path_mode='RELATIVE', reuse='REUSE', sequences='NONE', alpha_mode='DONT_CHANGE', parser=None):
            from bpy_extras.image_utils import load_image
            
            single_file = isinstance(filepaths, str)
            if single_file: parser = None
            filepaths = ({filepaths} if single_file else set(filepaths))
            
            from .bpy_inspect import bpy_file_filters
            image_exts = set(bpy_file_filters["image"])
            movie_exts = set(bpy_file_filters["movie"])
            allowed_exts = image_exts | movie_exts
            
            if (sequences != 'NONE') and (not single_file):
                movie_filepaths = {filepath for filepath in filepaths if os.path.splitext(filepath)[1].lower() in movie_exts}
                image_filepaths = {filepath for filepath in filepaths if filepath not in movie_filepaths}
                frame_sequences = BpyPath.frame_sequences(image_filepaths, (sequences == 'WITH_GAPS'))
                frame_sequences.extend((filepath, 1, 1) for filepath in movie_filepaths)
            else:
                frame_sequences = zip(filepaths, itertools.repeat(1), itertools.repeat(1))
            
            use_relpath = (path_mode == 'RELATIVE') and bpy.data.is_saved
            use_abspath = (path_mode == 'ABSOLUTE')
            use_alpha_mode = (alpha_mode != 'DONT_CHANGE')
            
            results = {}
            kwargs = dict(check_existing = (reuse != 'NONE'), force_reload = (reuse == 'RELOAD'))
            for filepath, index_start, frame_count in sorted(frame_sequences):
                ext = os.path.splitext(filepath)[1].lower()
                if ext not in allowed_exts: continue
                
                if parser:
                    shared_key, category_id = parser.parse(filepath)
                    if category_id is None: continue
                else:
                    shared_key, category_id = filepath, None
                
                if not single_file:
                    result = results.get(shared_key)
                    if result is None:
                        result = {}
                        results[shared_key] = result
                    
                    if category_id in result: continue
                
                image = load_image(filepath, directory, **kwargs)
                if not image: continue
                
                if max(image.size) <= 0: # e.g. corrupted file
                    if image.users == 0: bpy.data.images.remove(image)
                    continue
                
                if image.source == 'MOVIE':
                    # Blender bug: frame_duration returns 1
                    # on first read, so we have to read it twice
                    frame_count = image.frame_duration
                    frame_count = image.frame_duration
                elif frame_count > 1:
                    image.source = 'SEQUENCE'
                
                if use_relpath:
                    try:
                        image.filepath_raw = BpyPath.relpath(image.filepath_raw)
                    except ValueError:
                        pass # Can't always find relative path (e.g. between drive letters on Windows)
                elif use_abspath:
                    image.filepath_raw = BpyPath.abspath(image.filepath_raw)
                
                if use_alpha_mode: image.alpha_mode = alpha_mode
                
                if single_file: return image
                
                result[category_id] = (image, index_start, frame_count)
            
            return (None if single_file else results)

#============================================================================#

# depsgraph.object_instances does not preserve information
# about the hierarchy within instances, so it seems the only
# way to restore it is by "depth peeling" of sorts
class InstanceRealizer:
    def __init__(self, depsgraph, objs, use_base_parent=True, use_hierarchy=True):
        self.use_base_parent = use_base_parent
        self.use_hierarchy = use_hierarchy
        self.depsgraph = depsgraph
        self.scene_coll = self.depsgraph.scene.collection
        self.src_objs = set(objs)
        self.dst_objs = set()
        self.objs = set(self.src_objs)
        self.main_objs = {obj:obj for obj in self.src_objs}
        
        while self.objs:
            self.step()
        
        if not self.use_hierarchy:
            for obj in self.dst_objs:
                m = Matrix(obj.matrix_world)
                obj.parent = (self.main_objs[obj] if self.use_base_parent else None)
                obj.matrix_world = m
    
    def stash_info_obj(self, obj):
        if obj.instance_type == 'NONE': return
        self.infos[obj] = obj.instance_type
        obj.instance_type = 'NONE'
    
    def stash_info_coll(self, obj):
        coll = obj.instance_collection
        if not coll: return
        for subobj in coll.all_objects:
            self.stash_info_obj(subobj)
    
    def stash_info_childs(self, obj):
        children = self.child_map.get(obj)
        if not children: return
        for subobj in children:
            self.stash_info_obj(subobj)
            self.stash_info_childs(subobj)
    
    def stash_info(self):
        self.workset = {obj for obj in self.objs if obj.instance_type != 'NONE'}
        
        self.infos = {} # temporary instance_type storage
        for obj in self.workset:
            if obj.instance_type == 'COLLECTION':
                self.stash_info_coll(obj)
            elif obj.instance_type in ('VERTS', 'FACES'):
                self.stash_info_childs(obj)
        
        info_objs = set(self.infos.keys())
        self.objs = self.workset & info_objs
        self.workset -= info_objs
        self.depsgraph.update() #BlUtil.Scene.update(self.depsgraph.scene)
    
    def restore_info(self):
        for obj, info in self.infos.items():
            obj.instance_type = info
        self.depsgraph.update() #BlUtil.Scene.update(self.depsgraph.scene)
    
    def gather_instances(self):
        self.flat = {} # grouped by instancer objects
        for i in self.depsgraph.object_instances:
            obj = (i.parent if i.is_instance else i.object)
            obj = obj.original
            #obj = get_idblock(bpy.data.objects, obj.name_full)
            if obj not in self.workset: continue
            insts = self.flat.setdefault(obj, [])
            if i.is_instance:
                #iobj = get_idblock(bpy.data.objects, i.instance_object.name_full)
                iobj = i.instance_object.original
                insts.append((iobj, Matrix(i.matrix_world)))
    
    def obj_instances(self, obj):
        idict = {} # grouped by instancing objects
        for iobj, m in self.flat.get(obj, ()):
            idict.setdefault(iobj, []).append(m)
        return idict
    
    def restore_hierarchy(self, idict, parent_map):
        subtree = {}
        for iobj, ms in idict.items():
            ipar = parent_map.get(iobj)
            if ipar not in idict: ipar = None
            for index in range(len(ms)):
                key = ((ipar, index) if ipar else None)
                entry = (iobj, index, ms[index])
                subtree.setdefault(key, []).append(entry)
        return subtree
    
    def realize(self, mode, obj_colls, iobj, parent, m):
        obj = iobj.copy()
        self.scene_coll.objects.link(obj)
        
        # It seems that Blender does not put the "realized"
        # instances into any collections besides the scene
        # (at least that's the current behavior)
        # This can change the look of collection instances,
        # so perhaps a more conservative approach would be
        # to put them in the same collections [only if the
        # originals were there too: in case of vert/face].
        if mode == 'OR':
            for coll, coll_objs in obj_colls.items():
                coll.objects.link(obj)
        elif mode == 'AND':
            for coll, coll_objs in obj_colls.items():
                if iobj in coll_objs:
                    coll.objects.link(obj)
        
        # Blender also seems to remove all instancing on
        # the realized instances.
        #obj.instance_type = 'NONE' # ?
        
        obj.parent = parent
        obj.matrix_world = m
        
        self.main_objs[obj] = self.main_obj
        self.dst_objs.add(obj)
        if obj.instance_type != 'NONE': self.objs.add(obj)
        
        return obj
    
    def traverse(self, subtree, mode, obj_colls, parent, key, level=0):
        data = subtree.get(key)
        if data is None: return # leaf child
        
        for iobj, index, m in data:
            obj = self.realize(mode, obj_colls, iobj, parent, m)
            self.traverse(subtree, mode, obj_colls, obj, (iobj, index), level+1)
    
    def get_obj_collections(self, obj):
        result = {}
        for coll in bpy.data.collections:
            coll_objs = set(coll.objects)
            if obj not in coll_objs: continue
            result[coll] = coll_objs
        return result
    
    def process_obj(self, obj):
        idict = self.obj_instances(obj)
        if idict:
            if obj.instance_type == 'COLLECTION':
                child_map, parent_map = BlUtil.Collection.map_children(obj.instance_collection, self.child_cache)
                mode = 'OR'
            elif obj.instance_type in ('VERTS', 'FACES'):
                child_map, parent_map = self.child_map, self.parent_map
                mode = 'AND'
            subtree = self.restore_hierarchy(idict, parent_map)
            obj_colls = self.get_obj_collections(obj)
            parent = (obj if self.use_base_parent else None)
            self.main_obj = self.main_objs[obj]
            self.traverse(subtree, mode, obj_colls, parent, None)
        obj.instance_type = 'NONE'
    
    def step(self):
        self.child_cache = {}
        self.child_map, self.parent_map = BlUtil.Collection.map_children(self.scene_coll, self.child_cache)
        
        self.stash_info()
        self.gather_instances()
        self.restore_info()
        
        for obj in self.workset:
            self.process_obj(obj)
        self.depsgraph.update() #BlUtil.Scene.update(self.depsgraph.scene)

def make_instances_real(depsgraph, objs, use_base_parent=True, use_hierarchy=True):
    ir = InstanceRealizer(depsgraph, objs, use_base_parent, use_hierarchy)
    return {obj:ir.main_objs[obj] for obj in ir.dst_objs}

# =========================================================================== #

class NodeTreeComparer:
    @classmethod
    def link_key(cls, link):
        return (
            link.from_node.bl_idname,
            link.from_node.name,
            link.from_socket.bl_idname,
            link.from_socket.type,
            link.from_socket.identifier,
            link.from_socket.enabled,
            link.to_node.bl_idname,
            link.to_node.name,
            link.to_socket.bl_idname,
            link.to_socket.type,
            link.to_socket.identifier,
            link.to_socket.enabled,
        )
    
    @classmethod
    def socket_key(cls, socket):
        default_value = (BlRna.serialize_value(socket.default_value)
            if hasattr(socket, "default_value") else None)
        
        # Make sure we have immutable values (for hashing)
        if (socket.type in ('VECTOR', 'RGBA')) and (default_value is not None):
            default_value = tuple(default_value)
        
        return (socket.bl_idname, socket.identifier,
            socket.enabled, socket.type, default_value,
            tuple(cls.link_key(link) for link in socket.links))
    
    @classmethod
    def node_key(cls, node):
        idname = node.bl_idname # type of node
        name = node.name
        internal_links = frozenset(cls.link_key(link) for link in node.internal_links)
        parent = node.parent
        if parent is not None: parent = parent.name
        inputs = frozenset(cls.socket_key(socket) for socket in node.inputs)
        outputs = frozenset(cls.socket_key(socket) for socket in node.outputs)
        return (idname, name, parent, internal_links, inputs, outputs)
    
    @classmethod
    def nodetree_key(cls, nodetree):
        return frozenset(cls.node_key(node) for node in nodetree.nodes)
    
    @classmethod
    def compare(cls, treeA, treeB):
        if (treeA is None) and (treeB is None): return True
        if (treeA is None) or (treeB is None): return False
        if not BlRna.compare(treeA.animation_data, treeB.animation_data): return False
        return cls.nodetree_key(treeA) == cls.nodetree_key(treeB)

# =========================================================================== #

class ToggleObjectMode:
    def __init__(self, mode='OBJECT'):
        if not isinstance(mode, str):
            mode = ('OBJECT' if mode else None)
        
        self.mode = mode
    
    def __enter__(self):
        if self.mode:
            active_obj = bpy.context.object
            self.prev_mode = (active_obj.mode if active_obj else 'OBJECT')
            
            if self.prev_mode != self.mode:
                edit_preferences = bpy.context.preferences.edit
                self.global_undo = edit_preferences.use_global_undo
                edit_preferences.use_global_undo = False
                bpy.ops.object.mode_set(mode=self.mode)
        
        return self
    
    def __exit__(self, type, value, traceback):
        if self.mode:
            if self.prev_mode != self.mode:
                bpy.ops.object.mode_set(mode=self.prev_mode)
                edit_preferences = bpy.context.preferences.edit
                edit_preferences.use_global_undo = self.global_undo

#============================================================================#

class MeshEquivalent:
    # TODO: vertex groups? shape keys? face maps?
    # They are extrinsic to a bmesh, and typically
    # not needed for the purpose of baking.
    # If they are actually needed, it's probably
    # easier to just join the meshes.
    
    @classmethod
    def bake(cls, target, objs, depsgraph, collection=None, **kwargs):
        if isinstance(target, str):
            mode = ('OBJ' if collection else 'MESH')
            obj, mesh, name = None, None, target
        elif isinstance(target, bpy.types.Mesh):
            mode = 'MESH'
            obj, mesh, name = None, target, None
        elif isinstance(target, bpy.types.Object) and (target.type == 'MESH'):
            mode = 'OBJ'
            obj, mesh, name = target, target.data, None
        else:
            raise TypeError("target must be a string, a Mesh or a mesh Object")
        
        bm = bmesh.new()
        kwargs.update(bm=bm, materials={}, target=mesh)
        cls.gather(objs, depsgraph, **kwargs)
        if kwargs.get("triangulate"):
            bmesh.ops.triangulate(bm, faces=bm.faces)
        if not mesh: mesh = bpy.data.meshes.new(name)
        bm.to_mesh(mesh) # erases all previous geometry
        bm.free()
        
        materials = kwargs["materials"]
        materials = {i: mat for mat, i in materials.items()}
        mat_count_old = len(mesh.materials)
        mat_count_new = len(materials)
        
        for i in range(min(mat_count_old, mat_count_new)):
            mesh.materials[i] = materials[i]
        
        for i in range(mat_count_old, mat_count_new):
            mesh.materials.append(materials[i])
        
        for i in range(mat_count_new, mat_count_old):
            mesh.materials.pop()
        
        if mode == 'MESH': return mesh
        
        if not obj: obj = bpy.data.objects.new(name, mesh)
        
        if collection:
            try:
                collection.objects.link(obj)
            except RuntimeError:
                pass
        
        return obj
    
    @classmethod
    def gather(cls, objs, depsgraph, matrix=None, edit='CAGE', instances=True, **kwargs):
        # edit: NONE, DATA, CAGE (None/False are also accepted)
        
        if isinstance(objs, bpy.types.Object):
            objs = {objs}
        elif isinstance(objs, bpy.types.DepsgraphObjectInstance):
            objs = {objs.object}
        elif instances:
            objs = set(objs) # original (not evaluated) objects are expected here
        
        kwargs["depsgraph"] = depsgraph
        
        kwargs["add_to_lists"] = False
        
        add_obj = kwargs.get("add_obj")
        
        for obj in objs:
            if isinstance(obj, bpy.types.DepsgraphObjectInstance): obj = obj.object
            kwargs["matrix"] = (matrix @ obj.matrix_world if matrix else obj.matrix_world)
            result = cls._gather(obj, depsgraph, edit, kwargs)
            if add_obj: add_obj(obj, result)
        
        if not instances: return
        
        for i in depsgraph.object_instances:
            if not i.is_instance: continue
            obj = i.parent.original
            if obj not in objs: continue
            kwargs["matrix"] = (matrix @ i.matrix_world if matrix else i.matrix_world)
            result = cls._gather(i.instance_object, depsgraph, edit, kwargs)
            if add_obj: add_obj(obj, result)
    
    @classmethod
    def _gather(cls, obj, depsgraph, edit, kwargs):
        if obj.data and (obj.data == kwargs.get("target")): return
        
        obj_types = kwargs.get("obj_types")
        if obj_types and (obj.type not in obj_types): return
        
        if obj.is_evaluated: return cls.get(obj, **kwargs)
        
        modifier_info = None
        toggle_objmode = False
        use_data = False
        
        if (obj.mode == 'EDIT') and edit and (edit != 'NONE'):
            if (obj.type != 'MESH') or (edit != 'CAGE'):
                use_data = True
            else:
                # If we don't exit edit mode, obj.to_mesh(preserve_all_data_layers=True)
                # will cause access violations, most of the time resulting in a crash
                toggle_objmode = True
                # Note: if object has modifiers with show_on_cage = True,
                # the edit-mode elements will be displayed at the positions
                # evaluated for the last modifier with show_on_cage = True
                modifier_info = []
                for i in range(len(obj.modifiers)-1, -1, -1):
                    md = obj.modifiers[i]
                    if md.show_in_editmode and md.show_on_cage: break
                    modifier_info.append((md, md.show_viewport, md.show_render))
                    md.show_viewport = False
                    md.show_render = False
        elif obj.mode == 'SCULPT':
            toggle_objmode = True
        
        if depsgraph.updates:
            depsgraph.update()
        
        obj = obj.evaluated_get(depsgraph)
        
        with ToggleObjectMode(toggle_objmode):
            source = (obj.data if use_data else obj)
            result = cls.get(source, **kwargs)
        
        if modifier_info:
            for md, show_viewport, show_render in modifier_info:
                md.show_viewport = show_viewport
                md.show_render = show_render
        
        if depsgraph.updates:
            depsgraph.update()
        
        return result
    
    @classmethod
    def get(cls, source, **kwargs):
        # source: evaluated object, original object, object data
        # verts, edges, polys, tris, poly_ids: for basic geometry
        # bm: for full information (if bm is specified, "basic" args are ignored)
        # materials: materials
        # matrix: matrix
        # depsgraph: can be specified for bm, if all layers data is needed
        if not source: return
        
        extras = cls._init_args(kwargs)
        
        _get = getattr(cls, "_get_"+source.bl_rna.identifier, None)
        
        if _get:
            _get(source, extras)
            update_normals = extras["update_normals"]
            if update_normals: update_normals()
        
        return kwargs
    
    @classmethod
    def _init_args(cls, kwargs):
        matrix = kwargs.get("matrix")
        if matrix:
            convert_pos = (lambda p: matrix @ p)
        else:
            convert_pos = Vector
        update_normals = None
        
        bm = kwargs.get("bm")
        
        if bm:
            materials = kwargs.get("materials")
            if not isinstance(materials, (dict, list)):
                materials = {}
                kwargs["materials"] = materials
            
            mat_ids = []
            if isinstance(materials, dict):
                def mat_add(mat):
                    id = materials.setdefault(mat, len(materials))
                    mat_ids.append(id)
            else:
                def mat_add(mat):
                    id = len(materials)
                    materials.append(mat)
                    mat_ids.append(id)
            
            verts = []
            def vert_add(pos):
                verts.append(bm.verts.new(pos))
            edges = []
            def edge_add(indices):
                edges.append(bm.edges.new([verts[id] for id in indices]))
            polys = []
            def poly_add(indices):
                polys.append(bm.faces.new([verts[id] for id in indices]))
            tri_add = None
            poly_id_add = None
            
            if matrix:
                def update_normals():
                    for p in polys:
                        p.normal_update()
                    for v in verts:
                        v.normal_update()
            
            kwargs["verts"] = verts
            kwargs["edges"] = edges
            kwargs["polys"] = polys
            kwargs["tris"] = []
            kwargs["poly_ids"] = []
        else:
            mat_ids = None
            mat_add = cls._init_arg(kwargs, "materials")
            vert_add = cls._init_arg(kwargs, "verts")
            edge_add = cls._init_arg(kwargs, "edges", "verts")
            tri_add = cls._init_arg(kwargs, "tris", "verts")
            poly_add = cls._init_arg(kwargs, "polys", "verts")
            poly_id_add = cls._init_arg(kwargs, "poly_ids", "poly_ids", 'LAST', 'ID')
        
        return dict(kwargs=kwargs, convert_pos=convert_pos, update_normals=update_normals,
            bm=bm, mat_add=mat_add, mat_ids=mat_ids, vert_add=vert_add, edge_add=edge_add,
            tri_add=tri_add, poly_add=poly_add, poly_id_add=poly_id_add)
    
    @classmethod
    def _init_arg(cls, kwargs, name, start=None, start_mode='LEN', arg_type=None):
        value = kwargs.get(name)
        
        if isinstance(value, dict):
            return (lambda item: value.setdefault(item, len(value)))
        
        if isinstance(value, list):
            if start is None: return (lambda item: value.append(item))
            
            if start_mode == 'LEN':
                start = len(kwargs[start])
            elif start_mode == 'MAX':
                start = max(kwargs[start], default=0) + 1
            elif start_mode == 'LAST':
                start = kwargs[start]
                start = (start[-1] + 1 if start else 0)
            
            if arg_type == 'ID': return (lambda item: value.append(start+item))
            
            return (lambda item: value.append(tuple(start+id for id in item)))
        
        kwargs[name] = []
        return (value if callable(value) else None)
    
    @classmethod
    def _add_materials(cls, mats_iterable, extras):
        mat_add = extras["mat_add"]
        
        if mat_add:
            has_mats = False
            for mat in mats_iterable:
                if mat: mat = mat.original # important!
                mat_add(mat)
                has_mats = True
            
            if not has_mats: mat_add(None)
            
            extras["mat_add"] = None # prevent adding materials afterwards
    
    _convertible_obj_types = {'MESH', 'CURVE', 'SURFACE', 'FONT', 'META'}
    _representable_obj_types = {'GPENCIL':"GreasePencil", 'LATTICE':"Lattice", 'ARMATURE':"Armature"}
    
    @classmethod
    def _get_Object(cls, obj, extras):
        if obj.type in cls._convertible_obj_types:
            kwargs = extras["kwargs"]
            
            if obj.data == kwargs.get("target"): return
            
            bm = extras["bm"]
            depsgraph = ((kwargs.get("depsgraph") or bpy.context.evaluated_depsgraph_get()) if bm else None)
            
            cls._get_Object_materials(obj, extras)
            
            mesh = obj.to_mesh(preserve_all_data_layers=bool(bm), depsgraph=depsgraph)
            cls._get_Mesh(mesh, extras)
            obj.to_mesh_clear()
        
        elif (obj.type == 'ARMATURE') and (obj.mode != 'EDIT') and (obj.data.pose_position == 'POSE'):
            cls._get_Armature_pose(obj.pose, extras)
        
        elif obj.type in cls._representable_obj_types:
            cls._get_Object_materials(obj, extras)
            
            _get = getattr(cls, "_get_"+cls._representable_obj_types[obj.type], None)
            _get(obj.data, extras)
    
    @classmethod
    def _get_Object_materials(cls, obj, extras):
        cls._add_materials((ms.material for ms in obj.material_slots), extras)
    
    @classmethod
    def _get_Mesh(cls, mesh, extras):
        cls._add_materials(mesh.materials, extras)
        
        if mesh.is_editmode:
            bm0 = bmesh.from_edit_mesh(mesh)
            cls._get_Mesh_bmesh(bm0, extras)
        elif extras["bm"]:
            bm0 = bmesh.new()
            bm0.from_mesh(mesh)
            cls._get_Mesh_bmesh(bm0, extras, mesh=mesh)
            bm0.free()
        else:
            cls._get_Mesh_mesh(mesh, extras)
    
    @classmethod
    def _get_Mesh_mesh(cls, mesh, extras):
        convert_pos = extras["convert_pos"]
        vert_add = extras["vert_add"]
        edge_add = extras["edge_add"]
        poly_add = extras["poly_add"]
        tri_add = extras["tri_add"]
        poly_id_add = extras["poly_id_add"]
        
        if vert_add:
            for vert in mesh.vertices:
                vert_add(convert_pos(vert.co))
        
        if edge_add:
            for edge in mesh.edges:
                edge_add(tuple(edge.vertices))
        
        if poly_add:
            for poly in mesh.polygons:
                poly_add(tuple(poly.vertices))
        
        if tri_add or poly_id_add:
            mesh.calc_loop_triangles()
            
            if tri_add:
                for tri in mesh.loop_triangles:
                    tri_add(tuple(tri.vertices))
            
            if poly_id_add:
                for tri in mesh.loop_triangles:
                    poly_id_add(tri.polygon_index)
    
    @classmethod
    def _get_Mesh_bmesh(cls, bm0, extras, mesh=None):
        bm = extras["bm"]
        
        if bm:
            cls._get_Mesh_bmesh_full(bm, bm0, extras, mesh=mesh)
        else:
            cls._get_Mesh_bmesh_basic(bm0, extras)
    
    @classmethod
    def _get_Mesh_bmesh_full(cls, bm, bm0, extras, mesh=None):
        kwargs = extras["kwargs"]
        
        if mesh and (mesh == kwargs.get("target")): return
        
        mat_ids = extras["mat_ids"]
        convert_pos = extras["convert_pos"]
        update_normals = extras["update_normals"]
        copy_normals = not update_normals
        verts = kwargs["verts"]
        edges = kwargs["edges"]
        polys = kwargs["polys"]
        
        vert_layers = cls._ensure_bmesh_layers(bm0.verts, bm.verts)
        edge_layers = cls._ensure_bmesh_layers(bm0.edges, bm.edges)
        face_layers = cls._ensure_bmesh_layers(bm0.faces, bm.faces)
        loop_layers = cls._ensure_bmesh_layers(bm0.loops, bm.loops)
        
        mat_ids_max = len(mat_ids)-1
        def remap_mat_id(mat_id):
            return mat_ids[min(max(mat_id, 0), mat_ids_max)]
        
        if mesh: # do this *after* ensure_bmesh_layers()
            vert_count = len(mesh.vertices)
            if vert_count == 0: return
            
            matrix = kwargs.get("matrix")
            
            if kwargs.get("add_to_lists", True):
                bm.from_mesh(mesh, face_normals=bool(update_normals))
                
                # Note: bmesh element sequences can be sliced even without ensure_lookup_table()
                # BUT: it still seems to be an O(N) operation
                edge_count = len(mesh.edges)
                face_count = len(mesh.polygons)
                verts_new = bm.verts[-vert_count:]
                edges_new = bm.edges[-edge_count:]
                faces_new = bm.faces[-face_count:]
                verts.extend(verts_new)
                edges.extend(edges_new)
                polys.extend(faces_new)
                
                if matrix: bmesh.ops.transform(bm, matrix=matrix, space=Matrix(), verts=verts_new)
                
                for face in faces_new:
                    face.material_index = remap_mat_id(face.material_index)
            else:
                # When baking many objects, modifying a temporary mesh is much faster
                # than slicing a bmesh (O(bm0.verts) vs O(bm.verts)) and faster
                # than copying bmesh elements manually (and might use less memory).
                mesh = bpy.data.meshes.new("TemporaryMesh")
                bm0.to_mesh(mesh)
                
                if matrix: mesh.transform(matrix)
                
                for face in mesh.polygons:
                    face.material_index = remap_mat_id(face.material_index)
                
                bm.from_mesh(mesh, face_normals=bool(update_normals))
                
                bpy.data.meshes.remove(mesh)
            
            return
        
        # In Blender 2.8, these problems STILL were not fixed :-(
        # * This will result in error (keyword "dest" type 4 not working yet!)
        #   geom = bm_copy.verts[:] + bm_copy.edges[:] + bm_copy.faces[:]
        #   bmesh.ops.duplicate(bm_copy, geom=geom, dest=self.bm, use_select_history=False)
        #   (Though, even when it's fixed, we'll still have to remap material indices)
        # * BMesh elements' copy_from() doesn't seem to work on other-mesh elements either
        
        verts_new = bm.verts.new
        edges_new = bm.edges.new
        faces_new = bm.faces.new
        
        copy_bmesh_layers = cls._copy_bmesh_layers
        
        verts_map = {}
        for vS in bm0.verts:
            vD = verts_new(convert_pos(vS.co))
            if copy_normals: vD.normal = Vector(vS.normal)
            vD.hide = vS.hide
            vD.select = vS.select
            vD.tag = vS.tag
            copy_bmesh_layers(vert_layers, vS, vD)
            verts_map[vS] = vD
            verts.append(vD)
        
        for eS in bm0.edges:
            try:
                eD = edges_new(tuple(verts_map[vS] for vS in eS.verts))
            except ValueError: # edge already exists
                continue
            eD.seam = eS.seam
            eD.smooth = eS.smooth
            eD.hide = eS.hide
            eD.select = eS.select
            eD.tag = eS.tag
            copy_bmesh_layers(edge_layers, eS, eD)
            edges.append(eD)
        
        for fS in bm0.faces:
            try:
                fD = faces_new(tuple(verts_map[vS] for vS in fS.verts))
            except ValueError: # face already exists
                continue
            if copy_normals: fD.normal = Vector(fS.normal)
            fD.material_index = remap_mat_id(fS.material_index)
            fD.smooth = fS.smooth
            fD.hide = fS.hide
            fD.select = fS.select
            fD.tag = fS.tag
            copy_bmesh_layers(face_layers, fS, fD)
            for lS, lD in zip(fS.loops, fD.loops):
                copy_bmesh_layers(loop_layers, lS, lD)
            polys.append(fD)
    
    @classmethod
    def _copy_bmesh_layers(cls, layers, elemS, elemD):
        try:
            for layerS, layerD in layers:
                elemD[layerD] = elemS[layerS]
            return
        except AttributeError: # readonly / unsupported type
            pass
        
        for i in range(len(layers)-1, -1, -1):
            layerS, layerD = layers[i]
            try:
                elemD[layerD] = elemS[layerS]
            except AttributeError: # readonly / unsupported type
                layers.pop(i)
    
    @classmethod
    def _ensure_bmesh_layers(cls, seq_src, seq_dst):
        all_layers = []
        
        access_src = seq_src.layers
        access_dst = seq_dst.layers
        
        for name in dir(access_src):
            if name.startswith("_"): continue
            
            layers_src = getattr(access_src, name)
            layers_dst = getattr(access_dst, name)
            
            for key in layers_src.keys():
                if key not in layers_dst: layers_dst.new(key)
                all_layers.append((layers_src.get(key), layers_dst.get(key)))
        
        return all_layers
    
    @classmethod
    def _get_Mesh_bmesh_basic(cls, bm0, extras):
        convert_pos = extras["convert_pos"]
        vert_add = extras["vert_add"]
        edge_add = extras["edge_add"]
        poly_add = extras["poly_add"]
        tri_add = extras["tri_add"]
        poly_id_add = extras["poly_id_add"]
        
        vert_map = {}
        for vert in bm0.verts:
            vert_map[vert] = len(vert_map)
            if vert_add: vert_add(convert_pos(vert.co))
        
        if edge_add:
            for edge in bm0.edges:
                edge_add(tuple(vert_map[v] for v in edge.verts))
        
        tri_or_poly_id = bool(tri_add or poly_id_add)
        if poly_add or tri_or_poly_id:
            for poly_id, poly in enumerate(bm0.faces):
                poly_vert_ids = tuple(vert_map[v] for v in poly.verts)
                if poly_add: poly_add(poly_vert_ids)
                
                if tri_or_poly_id:
                    if len(poly_vert_ids) == 3:
                        if tri_add: tri_add(poly_vert_ids)
                        if poly_id_add: poly_id_add(poly_id)
                    else:
                        for tri in tessellate_polygon([[v.co for v in poly.verts]]):
                            if tri_add: tri_add(tuple(poly_vert_ids[vi] for vi in tri))
                            if poly_id_add: poly_id_add(poly_id)
    
    @classmethod
    def _get_Curve(cls, curve, extras):
        cls._add_materials(curve.materials, extras)
        
        convert_pos = extras["convert_pos"]
        vert_add = extras["vert_add"]
        edge_add = extras["edge_add"]
        
        vertex_count = 0
        
        for spline in curve.splines:
            for p in spline.bezier_points:
                v0 = vertex_count
                v1 = vertex_count+1
                v2 = vertex_count+2
                vertex_count += 3
                
                if vert_add:
                    vert_add(convert_pos(p.co))
                    vert_add(convert_pos(p.handle_left))
                    vert_add(convert_pos(p.handle_right))
                
                if edge_add:
                    edge_add((v0, v1))
                    edge_add((v0, v2))
            
            grid_start = vertex_count
            for p in spline.points:
                vertex_count += 1
                if vert_add: vert_add(convert_pos(p.co.to_3d()))
            
            if (vertex_count > grid_start) and edge_add:
                u_count = spline.point_count_u
                v_count = spline.point_count_v
                
                for v_id in range(v_count):
                    for u_id in range(1, u_count):
                        v0 = grid_start + (u_id-1) + v_id*u_count
                        v1 = grid_start + u_id + v_id*u_count
                        edge_add((v0, v1))
                
                for v_id in range(1, v_count):
                    for u_id in range(u_count):
                        v0 = grid_start + u_id + (v_id-1)*u_count
                        v1 = grid_start + u_id + v_id*u_count
                        edge_add((v0, v1))
    
    _get_SurfaceCurve = _get_Curve
    _get_TextCurve = _get_Curve
    
    @classmethod
    def _get_MetaBall(cls, meta, extras):
        cls._add_materials(meta.materials, extras)
        
        convert_pos = extras["convert_pos"]
        vert_add = extras["vert_add"]
        
        if vert_add:
            for element in meta.elements:
                vert_add(convert_pos(element.co))
    
    @classmethod
    def _get_GreasePencil(cls, gpencil, extras):
        cls._add_materials(gpencil.materials, extras)
        
        convert_pos = extras["convert_pos"]
        vert_add = extras["vert_add"]
        edge_add = extras["edge_add"]
        poly_add = extras["poly_add"]
        tri_add = extras["tri_add"]
        poly_id_add = extras["poly_id_add"]
        
        poly_id = 0
        vertex_count = 0
        
        for layer in gpencil.layers:
            if layer.hide: continue
            
            frame = layer.active_frame
            for stroke in frame.strokes:
                poly_id += 1
                v_start = vertex_count
                vertex_count += len(stroke.points)
                if vertex_count == v_start: continue
                
                if vert_add:
                    for p in stroke.points:
                        vert_add(convert_pos(p.co))
                
                if edge_add:
                    for vi in range(v_start, vertex_count-1):
                        edge_add((vi, vi+1))
                    
                    if stroke.draw_cyclic:
                        edge_add((v_start, vertex_count-1))
                
                if poly_add and stroke.triangles:
                    poly_add(tuple(range(v_start, vertex_count)))
                
                if tri_add or poly_id_add:
                    for t in stroke.triangles:
                        if tri_add: tri_add((t.v1+v_start, t.v2+v_start, t.v3+v_start))
                        if poly_id_add: poly_id_add(poly_id)
    
    @classmethod
    def _get_Lattice(cls, lattice, extras):
        convert_pos = extras["convert_pos"]
        vert_add = extras["vert_add"]
        edge_add = extras["edge_add"]
        
        if vert_add:
            # Note: in edit mode, co_deform actually returns edit-mode
            # (not deformed) coordinates, which is exactly what we want
            for p in lattice.points:
                vert_add(convert_pos(p.co_deform))
        
        if edge_add:
            u_count = lattice.points_u
            v_count = lattice.points_v
            w_count = lattice.points_w
            
            offset = 1
            for w_id in range(w_count):
                for v_id in range(v_count):
                    for u_id in range(1, u_count):
                        vert_id = u_id + u_count*(v_id + v_count*w_id)
                        edge_add((vert_id-offset, vert_id))
            
            offset = u_count
            for w_id in range(w_count):
                for v_id in range(1, v_count):
                    for u_id in range(u_count):
                        vert_id = u_id + u_count*(v_id + v_count*w_id)
                        edge_add((vert_id-offset, vert_id))
            
            offset = u_count*v_count
            for w_id in range(1, w_count):
                for v_id in range(v_count):
                    for u_id in range(u_count):
                        vert_id = u_id + u_count*(v_id + v_count*w_id)
                        edge_add((vert_id-offset, vert_id))
    
    @classmethod
    def _get_Armature(cls, armature, extras):
        convert_pos = extras["convert_pos"]
        vert_add = extras["vert_add"]
        edge_add = extras["edge_add"]
        
        vertex_count = 0
        
        if armature.is_editmode:
            for bone in armature.edit_bones:
                vertex_count += 2
                
                if vert_add:
                    vert_add(convert_pos(bone.head))
                    vert_add(convert_pos(bone.tail))
                
                if edge_add:
                    edge_add((vertex_count-2, vertex_count-1))
        else:
            for bone in armature.bones:
                vertex_count += 2
                
                if vert_add:
                    vert_add(convert_pos(bone.head_local))
                    vert_add(convert_pos(bone.tail_local))
                
                if edge_add:
                    edge_add((vertex_count-2, vertex_count-1))
    
    @classmethod
    def _get_Armature_pose(cls, pose, extras):
        convert_pos = extras["convert_pos"]
        vert_add = extras["vert_add"]
        edge_add = extras["edge_add"]
        
        vertex_count = 0
        
        for bone in pose.bones:
            vertex_count += 2
            
            if vert_add:
                vert_add(convert_pos(bone.head))
                vert_add(convert_pos(bone.tail))
            
            if edge_add:
                edge_add((vertex_count-2, vertex_count-1))

#============================================================================#

class TextureFilenameParser:
    # Decision: we assume that, among the selected files,
    # all filenames include a texture/map type near
    # the end of the filename (people tend to naturally
    # use such naming anyway, since in that case all maps
    # related to the same material will be sorted
    # beside each other, as a contiguous block).
    
    separators = re.compile(r"[,;/|\\]")
    vowels = re.compile("[aeiouy]")
    
    # Note: if pattern contains group(s), re.split() will include separators into the result
    letters_or_numbers = re.compile(r"(\d+|[^\W\d_]+)", flags=re.UNICODE)
    
    def __init__(self, categories=None):
        self.categories = []
        self.synonyms = []
        self.tree = None
        self.filter = None
        
        if categories:
            for category in categories:
                self.add(category)
    
    def add(self, category):
        subsynonyms = set()
        for synonym in self.separators.split(category.lower()):
            self._add_synonym(subsynonyms, synonym)
        
        self.categories.append(category)
        self.synonyms.append(subsynonyms)
        
        return len(self.categories) - 1
    
    def _add_synonym(self, subsynonyms, synonym):
        synonym = " ".join(self.letters_or_numbers.split(synonym)[1::2])
        self._add_synonym_variants(subsynonyms, synonym)
        self._add_synonym_variants(subsynonyms, synonym+" map", False)
    
    def _add_synonym_variants(self, subsynonyms, synonym, add_consonants=True):
        synonym = " ".join(synonym.split())
        
        synonym_joined = synonym.replace(" ", "")
        subsynonyms.add((synonym_joined,))
        
        synonym_parts = synonym.split()
        subsynonyms.add(tuple(synonym_parts))
        
        if not add_consonants: return
        
        # If starts with a vowel, consonant-only variant will be confusing
        if self.vowels.match(synonym): return
        
        consonants = self.vowels.sub("", synonym)
        
        consonants_joined = consonants.replace(" ", "")
        if len(consonants_joined) < 3: return
        subsynonyms.add((consonants_joined,))
        
        consonants_parts = consonants.split()
        if min(len(part) for part in consonants_parts) < 3: return
        subsynonyms.add(tuple(consonants_parts))
    
    def find(self, category, add=True):
        result = [] # not a set, because order indicates priority
        
        for synonym in self.separators.split(category.lower()):
            synonym = tuple(self.letters_or_numbers.split(synonym)[1::2])
            if not synonym: continue
            
            for category_id, subsynonyms in enumerate(self.synonyms):
                if (synonym in subsynonyms) and (category_id not in result):
                    result.append(category_id)
                    break
            else:
                if add:
                    synonym = " ".join(synonym)
                    subsynonyms = set()
                    self._add_synonym(subsynonyms, synonym)
                    
                    self.categories.append(synonym)
                    self.synonyms.append(subsynonyms)
                    result.append(len(self.categories) - 1)
        
        return result
    
    def build_tree(self):
        self.tree = {}
        for category_id, subsynonyms in enumerate(self.synonyms):
            for synonym in subsynonyms:
                self._add_synonym_node(self.tree, category_id, synonym, 0, len(synonym)-1)
    
    def _add_synonym_node(self, tree, category_id, synonym, char_id, part_id, min_len=3):
        part = synonym[part_id]
        c = part[char_id]
        
        value = tree.get(c)
        if value is None:
            value = [None, {}, {}]
            tree[c] = value
        
        if (part_id == 0) and (value[0] is None):
            if char_id >= min(min_len, len(part))-1:
                value[0] = category_id
        
        char_id += 1
        if char_id < len(part):
            self._add_synonym_node(value[1], category_id, synonym, char_id, part_id, min_len)
            if char_id < min_len: return
        
        part_id -= 1
        if part_id >= 0:
            self._add_synonym_node(value[2], category_id, synonym, 0, part_id, min_len)
    
    def print_tree(self, tree=None, indent="", prefix=""):
        if tree is None:
            if self.tree is None: return
            tree = self.tree
        
        for key in sorted(tree):
            value = tree[key]
            print(f"{indent}{prefix}{key}: {value[0]}")
            self.print_tree(value[1], indent+"  ", "")
            self.print_tree(value[2], indent+"  ", "#")
    
    # returns (case-insensitive key, category id)
    def parse(self, filepath):
        filepath = filepath.lower()
        
        filedir, filename = os.path.split(filepath)
        filename = os.path.splitext(filename)[0]
        parts = self.letters_or_numbers.split(filename)
        
        for match in self._find_synonym_matches(parts):
            category_id, match_start, match_end = match
            if self.filter and (category_id not in self.filter): continue
            
            basename = "".join(parts[:match_start])
            return os.path.join(filedir, basename), category_id
        
        return filepath, None
    
    def _find_synonym_matches(self, parts):
        if len(parts) < 3: return
        
        if self.tree is None: self.build_tree()
        
        parts = [part.lower() for part in parts]
        
        for part_id in range(len(parts)-2, -1, -2):
            if not parts[part_id]: continue
            
            result = self._find_synonym_match(self.tree, parts, part_id, 2)
            if result[0] is None: continue
            
            yield result[0], result[1], part_id
    
    def _find_synonym_match(self, tree, parts, part_id, part_step):
        category_id = None
        match_id = part_id
        
        for part_id in range(part_id, -1, -part_step):
            part = parts[part_id]
            if not part: continue # just in case
            
            char_count = len(part)
            for c in part:
                value = tree.get(c)
                if value is None: return category_id, match_id
                char_count -= 1
                tree = (value[1] if char_count > 0 else value[2])
            
            if value[0] is not None:
                category_id = value[0]
                match_id = part_id
            
            if not tree: return category_id, match_id
        
        return category_id, match_id

#============================================================================#

class BpyPath:
    @staticmethod
    def normslash(path):
        return path.replace(os.path.sep, "/")
    
    @staticmethod
    def join(*paths):
        # use os.path.join logic (it's not that simple)
        return BpyPath.normslash(os.path.join(*paths))
    
    @staticmethod
    def splitext(path):
        path = BpyPath.normslash(path)
        i_split = path.rfind(".")
        if i_split < 0: return (path, "")
        return (path[:i_split], path[i_split:])
    
    # For some reason, when path contains "//", os.path.split ignores single slashes
    # When path ends with slash, return dir without slash, except when it's / or //
    @staticmethod
    def split(path):
        path = BpyPath.normslash(path)
        i_split = path.rfind("/") + 1
        dir_part = path[:i_split]
        file_part = path[i_split:]
        dir_part_strip = dir_part.rstrip("/")
        if dir_part_strip: dir_part = dir_part[:len(dir_part_strip)]
        return (dir_part, file_part)
    
    @staticmethod
    def abspath(path, *, start=None, library=None):
        return bpy.path.abspath(path, start=start, library=library)
    
    @staticmethod
    def relpath(path):
        try:
            # May cause ValueError if path is on a
            # different mount that the current .blend
            return bpy.path.relpath(path)
        except ValueError:
            return path
    
    @staticmethod
    def clean_name(name, *, replace='_'):
        return bpy.path.clean_name(name, replace=replace)
    
    @staticmethod
    def display_name(name, *, has_ext=True, title_case=True):
        # title_case argument was introduced in Blender 2.93
        # Unlike title(), it modifies only lowercase letters
        name = bpy.path.display_name(name, has_ext=has_ext)
        if title_case:
            name = "".join((t if c.islower() else c) for c, t in zip(name, name.title()))
        return name
    
    @staticmethod
    def display_name_to_filepath(name):
        return bpy.path.display_name_to_filepath(name)
    
    @staticmethod
    def display_name_from_filepath(name):
        return bpy.path.display_name_from_filepath(name)
    
    @staticmethod
    def ensure_ext(filepath, ext, *, case_sensitive=False):
        return bpy.path.ensure_ext(filepath, ext, case_sensitive=case_sensitive)
    
    @staticmethod
    def is_subdir(path, directory):
        return bpy.path.is_subdir(path, directory)
    
    @staticmethod
    def module_names(path, *, recursive=False):
        return bpy.path.module_names(path, recursive=False)
    
    @staticmethod
    def native_pathsep(path):
        return bpy.path.native_pathsep(path)
    
    @staticmethod
    def reduce_dirs(dirs):
        return bpy.path.reduce_dirs(dirs)
    
    @staticmethod
    def resolve_ncase(path):
        return bpy.path.resolve_ncase(path)
    
    @staticmethod
    def dirname(path):
        return BpyPath.split(path)[0]
    
    @staticmethod
    def basename(path):
        return BpyPath.split(path)[1]
    
    @staticmethod
    def replace_extension(path, ext):
        name = BpyPath.basename(path)
        if name and not name.lower().endswith(ext.lower()):
            path = BpyPath.splitext(path)[0] + ext
        return path
    
    forbidden_chars = "\x00-\x1f/" # on all OSes
    forbidden_chars += "<>:\"|?*\\\\" # on Windows/FAT/NTFS
    forbidden_chars = "["+forbidden_chars+"]"
    @staticmethod
    def clean_filename(filename, sub="-"):
        return re.sub(BpyPath.forbidden_chars, sub, filename)
    
    regex_number = re.compile("[0-9]")
    regex_numbers = re.compile("[0-9]+")
    @staticmethod
    def frame_sequences(file_paths, allow_gaps=False):
        groups = collections.defaultdict(list)
        for file_path in file_paths:
            file_dir, file_name = BpyPath.split(file_path)
            key = (file_dir, BpyPath.regex_number.sub("/", file_name))
            indices = tuple(map(int, BpyPath.regex_numbers.findall(file_name)))
            groups[key].append((indices, file_path))
        
        no_gaps = not allow_gaps
        
        def find_axis(prev, curr):
            if not prev: return -1
            axis = -1
            for i in range(len(curr)):
                delta = curr[i] - prev[i]
                if delta == 0: continue
                if no_gaps and (delta != 1): return -1
                if axis >= 0: return -1
                axis = i
            return axis
        
        results = []
        for group in groups.values():
            group.sort()
            prev, axis = None, -1
            for curr, file_path in group:
                new_axis = find_axis(prev, curr)
                if (new_axis < 0) or ((axis >= 0) and (new_axis != axis)):
                    results.append([file_path, curr or (0,), 0, 1])
                    new_axis = -1
                else:
                    result = results[-1]
                    result[2] = new_axis
                    result[3] = (curr[new_axis] - result[1][new_axis]) + 1
                prev, axis = curr, new_axis
        
        return [(file_path, indices[axis], count) for file_path, indices, axis, count in results]
    
    @staticmethod
    def properties(obj, raw=None, no_readonly=False, names=False):
        # Inspired by BKE_bpath_traverse_id(...) in source\blender\blenkernel\intern\bpath.c
        
        if (not obj) or (not hasattr(obj, "bl_rna")): return
        
        if no_readonly and isinstance(obj, bpy.types.ID) and obj.library: return
        
        rna_props = obj.bl_rna.properties
        
        for p in rna_props:
            if not BpyPath.is_path_property(p): continue
            if p.is_readonly and no_readonly: continue
            
            if raw is None:
                yield obj, (p.identifier if names else p)
            elif p.identifier.endswith("_raw"):
                if raw: yield obj, (p.identifier if names else p)
            elif (not raw) or (p.identifier+"_raw" not in rna_props):
                yield obj, (p.identifier if names else p)
        
        point_cache = getattr(obj, "point_cache", None)
        if isinstance(point_cache, bpy.types.PointCache):
            yield from BpyPath.properties(point_cache, raw, no_readonly, names)
        
        if isinstance(obj, bpy.types.Object):
            for modifier in obj.modifiers:
                yield from BpyPath.properties(modifier, raw, no_readonly, names)
            for particle_system in obj.particle_systems:
                yield from BpyPath.properties(particle_system, raw, no_readonly, names)
        elif isinstance(obj, bpy.types.Modifier):
            if obj.type == 'FLUID': # Blender >= 2.82
                yield from BpyPath.properties(obj.domain_settings, raw, no_readonly, names)
            elif obj.type == 'FLUID_SIMULATION': # Blender < 2.82
                yield from BpyPath.properties(obj.settings, raw, no_readonly, names)
            elif obj.type == 'SMOKE': # Blender < 2.82
                yield from BpyPath.properties(obj.domain_settings, raw, no_readonly, names)
        elif isinstance(obj, bpy.types.PointCache):
            for point_cache_item in obj.point_caches:
                yield from BpyPath.properties(point_cache_item, raw, no_readonly, names)
        elif isinstance(obj, bpy.types.Material):
            yield from BpyPath.properties(obj.node_tree, raw, no_readonly, names)
        elif isinstance(obj, bpy.types.NodeTree):
            for node in obj.nodes:
                yield from BpyPath.properties(node, raw, no_readonly, names)
        elif isinstance(obj, bpy.types.Scene):
            yield from BpyPath.properties(obj.rigidbody_world, raw, no_readonly, names)
            yield from BpyPath.properties(obj.sequence_editor, raw, no_readonly, names)
        elif isinstance(obj, bpy.types.SequenceEditor):
            for sequence in obj.sequences_all:
                yield from BpyPath.properties(sequence, raw, no_readonly, names)
        elif isinstance(obj, bpy.types.Sequence):
            # Not all sequence subtypes have proxy or elements
            yield from BpyPath.properties(getattr(obj, "proxy", None), raw, no_readonly, names)
            for element in getattr(obj, "elements", ()):
                yield from BpyPath.properties(element, raw, no_readonly, names)
    
    path_property_subtypes = {'FILE_PATH', 'DIR_PATH', 'FILE_NAME'}
    @staticmethod
    def is_path_property(p):
        if p.type != 'STRING': return False
        if p.subtype in BpyPath.path_property_subtypes: return True
        # Not all file/dir path-like properties are marked with a subtype
        p_id_low = p.identifier.lower()
        conditionA = ("file" in p_id_low) or ("dir" in p_id_low) or ("cache" in p_id_low)
        conditionB = ("path" in p_id_low) or ("name" in p_id_low) or ("proxy" in p_id_low)
        conditionC = ("directory" in p_id_low)
        return (conditionA and conditionB) or conditionC

BpyPath.operator_presets_dir = BpyPath.join(bpy.utils.resource_path('USER'), "scripts", "presets", "operator")
