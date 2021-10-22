# ##### BEGIN GPL LICENSE BLOCK #####
#
#  This program is free software; you can redistribute it and/or
#  modify it under the terms of the GNU General Public License
#  as published by the Free Software Foundation; either version 2
#  of the License, or (at your option) any later version.
#
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.
#
#  You should have received a copy of the GNU General Public License
#  along with this program; if not, write to the Free Software Foundation,
#  Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301, USA.
#
# ##### END GPL LICENSE BLOCK #####

bl_info = {
    "name": "Cut / Copy / Paste",
    "author": "dairin0d, moth3r",
    "version": (2, 0, 3),
    "blender": (2, 80, 0),
    "location": "3D View",
    "description": "Advanced cut/copy/paste for objects and elements",
    "warning": "",
    "wiki_url": "https://github.com/dairin0d/blender-2.8-scripts/blob/master/docs/cut_copy_paste/cut_copy_paste.md",
    "doc_url": "https://github.com/dairin0d/blender-2.8-scripts/blob/master/docs/cut_copy_paste/cut_copy_paste.md",
    "tracker_url": "https://github.com/dairin0d/blender-2.8-scripts/issues/new?labels=cut+copy+paste",
    "category": "3D View",
}

# =========================================================================== #

import os
import sys
import math
import json
import base64
import bz2
import traceback

import bpy
from mathutils import Vector, Matrix, Quaternion, Euler, Color

import importlib
import importlib.util

if "dairin0d" in locals(): importlib.reload(dairin0d)
exec(("" if importlib.util.find_spec("dairin0d") else "from . ")+"import dairin0d")

dairin0d.load(globals(), {
    "bpy_inspect": "prop, BpyData, BlRna, BlEnums",
    "utils_python": "get_or_add",
    "utils_ui": "NestedLayout",
    "utils_blender": "BlUtil, BpyPath, NodeTreeComparer, MeshEquivalent",
    "utils_addon": "AddonManager",
})

addon = AddonManager()
settings = addon.settings

# =========================================================================== #

# * There is already a built-in copy/paste pose operator (though it only applies
#   "globally" by bone names, and has no option to paste to a sub-hierarchy).
# * Text edit mode also has copy/paste (plain text).
# * Node editor has copy/paste (though it only works locally).
# * There seems to be no meaningful copy/paste for particles/lattice.
# * Surface copy/paste is quite limited, since only whole patches can be safely pasted.

# Do we really need full text serialization? Typically it's only needed
# to send something over the network.
# For objects, it's easier to just Export Selected and send the resulting file
# (although it won't have some features like after-Paste postprocessing).
# For elements, there is no equivalent (implementing something similar
# in Export Selected is possible, though it can't create batches,
# and probably isn't really useful anyway).

# location:
# * global: cursor, world origin, original global
# * local: parent, original local
# [optional]: move to mouse afterwards

# rotation:
# * global: relative to view, no rotation, original global
# * local: aligned to parent, original local

class ClipboardUtil:
    content_key = "Blender Clipboard"
    
    name = "clipboard"
    
    modes_items = [
        ('OBJECT', "Object", "Object mode"),
        ('EDIT_MESH', "Mesh", "Edit Mesh mode"),
        ('EDIT_CURVE', "Curve", "Edit Curve mode"),
        ('EDIT_SURFACE', "Surface", "Edit Surface mode"),
        ('EDIT_METABALL', "Metaball", "Edit Metaball mode"),
        ('EDIT_GPENCIL', "Grease Pencil", "Edit Grease Pencil mode"),
        ('EDIT_ARMATURE', "Armature", "Edit Armature mode"),
    ]
    modes = {item[0] for item in modes_items}
    
    @classmethod
    def is_view3d(cls, context):
        return (context.area.type == 'VIEW_3D') and (context.region.type == 'WINDOW')
    
    @classmethod
    def get_mesh_delete_type(cls, context=None):
        if not context: context = bpy.context
        mesh_select_mode = context.tool_settings.mesh_select_mode
        if mesh_select_mode[0]: return 'VERT'
        if mesh_select_mode[1]: return 'EDGE'
        return 'FACE'
    
    @classmethod
    def get_view_rotation(cls, context):
        rv3d = context.region_data
        if rv3d.view_perspective != 'CAMERA': return rv3d.view_rotation
        v3d = context.space_data
        return v3d.camera.matrix_world.to_quaternion()
    
    @classmethod
    def serialize_matrix(cls, matrix):
        # Last row is always [0,0,0,1], so we can save a bit of space
        return [tuple(v) for v in matrix[:3]]
    
    @classmethod
    def deserialize_matrix(cls, rows):
        matrix = (Matrix(rows) if len(rows) == 4 else Matrix((*rows, (0,0,0,1))))
        assert (len(matrix) == 4) and (len(matrix[0]) == 4)
        return matrix
    
    @classmethod
    def serialize_view_rotation(cls, context):
        return (tuple(cls.get_view_rotation(context)) if cls.is_view3d(context) else None)
    
    @classmethod
    def get_clipboards_dir(cls):
        blender_tempdir = bpy.app.tempdir
        if (blender_tempdir[-1] in "\\/"): blender_tempdir = blender_tempdir[:-1]
        blender_tempdir = os.path.dirname(blender_tempdir)
        
        clipboards_path = os.path.normcase(os.path.join(blender_tempdir, "blender_clipboards"))
        return clipboards_path
    
    @classmethod
    def get_clipboard_path(cls, name):
        return os.path.join(cls.get_clipboards_dir(), f"{name}.blend")
    
    @classmethod
    def prepare_clipboard_path(cls, name):
        clipboards_path = cls.get_clipboards_dir()
        if not os.path.exists(clipboards_path): os.makedirs(clipboards_path)
        
        clipboard_path = os.path.join(clipboards_path, f"{name}.blend")
        if os.path.isfile(clipboard_path): os.remove(clipboard_path)
        
        return clipboard_path
    
    @classmethod
    def read_b64(cls, path):
        with open(path, "rb") as file:
            data = file.read()
        
        return base64.b64encode(data).decode('ascii')
    
    @classmethod
    def write_b64(cls, path, data):
        data = base64.b64decode(data.encode('ascii'))
        
        with open(path, "wb") as file:
            file.write(data)
    
    @classmethod
    def get_undo(cls):
        edit_preferences = bpy.context.preferences.edit
        return dict(
            use_global_undo = edit_preferences.use_global_undo,
            undo_steps = edit_preferences.undo_steps,
            undo_memory_limit = edit_preferences.undo_memory_limit,
        )
    
    @classmethod
    def set_undo(cls, use_global_undo, undo_steps, undo_memory_limit):
        edit_preferences = bpy.context.preferences.edit
        edit_preferences.use_global_undo = use_global_undo
        edit_preferences.undo_steps = undo_steps
        edit_preferences.undo_memory_limit = undo_memory_limit
    
    @classmethod
    def write(cls, name, datablocks, pack, preprocess, id_mapper, id_map):
        clipboard_path = cls.prepare_clipboard_path(name)
        
        # bpy.data.libraries.write() expects a set
        if not isinstance(datablocks, set): datablocks = set(datablocks)
        
        use_undo = bool(pack or preprocess)
        
        if use_undo:
            undo_options_prev = cls.get_undo()
            cls.set_undo(True, 256, 0)
            
            bpy.ops.ed.undo_push(message="Copy")
            
            if preprocess: preprocess()
        
        if pack:
            while cls.make_everything_local(datablocks, id_mapper, id_map): pass
            
            bpy.ops.file.make_paths_relative()
            bpy.ops.file.pack_all()
        
        # Trying to support local-only paths for packed clipboard
        # would add too much complexity for too little practical utility.
        # It's easier to just remap all paths to absolute (so that
        # Blender won't append with paths relative to the clipboard file).
        
        kwargs = {"compress": pack, "fake_user": False}
        
        if bpy.app.version < (2, 90, 0):
            # relative_remap = True remaps only to absolute paths,
            # so the pasted datablocks will always have absolute paths
            kwargs["relative_remap"] = True
        else:
            # path_remap allows to actually preserve relative paths
            if pack:
                kwargs["path_remap"] = 'ABSOLUTE'
            elif bpy.data.is_saved:
                kwargs["path_remap"] = 'RELATIVE'
            else:
                kwargs["path_remap"] = 'ABSOLUTE'
        
        # Note: explicitly including datablocks from other libraries
        # does not pack/localize them into the written library
        bpy.data.libraries.write(clipboard_path, datablocks, **kwargs)
        
        if use_undo:
            bpy.ops.ed.undo() # may print "Checking (...) against (...) FOUND!"
            
            cls.set_undo(**undo_options_prev)
        
        return (cls.read_b64(clipboard_path) if pack else None)
    
    @classmethod
    def make_everything_local(cls, target_datablocks, id_mapper, id_map):
        priority_before = [
            'workspaces',
            'scenes',
            'collections',
            'objects',
            'node_groups',
            'brushes',
            'palettes',
            'paint_curves',
            'masks',
            'linestyles',
            'worlds'
        ]
        
        priority_after = [
            'textures',
            'materials',
            'actions',
            'images',
            'movieclips',
            'sounds',
            'fonts'
        ]
        
        exclude = [
            'screens',
            'cache_files',
            'libraries',
            'window_managers'
        ]
        
        # Object data and some other stuff
        # (Blender may introduce new data types,
        # so it's best not to make any explicit lists)
        priority_middle = set(BpyData.data_names())
        priority_middle.difference_update(priority_before)
        priority_middle.difference_update(priority_after)
        priority_middle.difference_update(exclude)
        
        modified = False
        
        for data_name in priority_before:
            modified |= cls.make_local(data_name, target_datablocks, id_mapper, id_map)
        
        for data_name in priority_middle:
            modified |= cls.make_local(data_name, target_datablocks, id_mapper, id_map)
        
        for data_name in priority_after:
            modified |= cls.make_local(data_name, target_datablocks, id_mapper, id_map)
        
        return modified
    
    @classmethod
    def make_local(cls, datablocks, target_datablocks, id_mapper, id_map):
        if isinstance(datablocks, str): datablocks = getattr(bpy.data, datablocks, None)
        
        if not datablocks: return False
        
        modified = False
        
        for datablock in datablocks:
            if not datablock.library: continue
            
            was_present = (datablock in target_datablocks)
            
            if was_present:
                old_id = id_mapper(datablock)
                target_datablocks.discard(datablock)
            
            local_datablock = datablock.make_local()
            
            modified = True
            
            if was_present:
                new_id = id_mapper(local_datablock)
                target_datablocks.add(local_datablock)
                
                if old_id != new_id:
                    id_map[old_id] = new_id
            
            if datablock and (local_datablock != datablock):
                datablock.user_remap(local_datablock)
        
        return modified
    
    @classmethod
    def get_all_datablocks(cls):
        result = set()
        for data_name in BpyData.data_names():
            data = getattr(bpy.data, data_name, None)
            if data: result.update(data)
        return result
    
    @classmethod
    def get_indirect_refs(cls, datablocks):
        other_datablocks = cls.get_all_datablocks()
        other_datablocks.difference_update(datablocks)
        
        indirect_refs = set()
        for other_datablock in other_datablocks:
            for datablock in datablocks:
                if not datablock.user_of_id(other_datablock): continue
                indirect_refs.add(other_datablock)
        
        return indirect_refs
    
    @classmethod
    def normpath(cls, path):
        return os.path.normcase(bpy.path.abspath(path))
    
    @classmethod
    def get_library_path(cls, datablock):
        if not datablock: return bpy.data.filepath
        if not isinstance(datablock, bpy.types.ID): return None
        if isinstance(datablock, bpy.types.Library): return cls.normpath(datablock.filepath)
        return cls.get_library_path(datablock.library)
    
    @classmethod
    def get_library_id(cls, library_paths, library_ids, datablock):
        if isinstance(datablock, bpy.types.Library):
            library = datablock
        else:
            library = (datablock.library if datablock else None)
        
        library_id = library_ids.get(library)
        
        if library_id is None:
            path = cls.get_library_path(library) or ""
            library_id = len(library_paths)
            library_paths.append(path)
            library_ids[library] = library_id
        
        return library_id
    
    @classmethod
    def get_link_info(cls, datablocks, library_paths, library_ids):
        if not isinstance(datablocks, set): datablocks = set(datablocks)
        
        link_info = {}
        
        for datablock in datablocks:
            if not datablock: continue
            
            type_name = datablock.bl_rna.identifier
            data_name = BpyData.get_data_name(type_name)
            
            library_id = cls.get_library_id(library_paths, library_ids, datablock)
            
            datas = get_or_add(link_info, library_id, dict)
            get_or_add(datas, data_name, list).append(datablock.name)
        
        return link_info
    
    @classmethod
    def filter_edit_mode_objects(cls, objs, active_obj):
        objs_of_type = {obj for obj in objs if obj.type == active_obj.type}
        
        objs = {active_obj} # in non-Object modes, make sure that active object is included
        
        # Grease Pencil objects don't support multi-object edit mode (at least as of Blender 2.92)
        if active_obj.type != 'GPENCIL': objs.update(objs_of_type)
        
        return objs

@addon.Operator(idname="view3d.copy", label="Copy", description="Copy objects/elements")
class OperatorCopy:
    mode: 'COPY' | prop("Mode", "Copy mode", items=[
        ('COPY', "Copy", "Copy (write a temporary file)"),
        ('COPY_TEXT', "Copy as Text", "Copy (store all data in a text clipboard)"),
        ('COPY_LINKED', "Copy Linked", "Copy (don't write a temporary file if data can be linked)"),
    ])
    
    @classmethod
    def poll(cls, context):
        return context.mode in addon.preferences.modes
    
    def execute(self, context):
        objs = set(context.selected_objects)
        active_obj = context.active_object
        
        preprocess = None
        
        if context.mode != 'OBJECT':
            objs = ClipboardUtil.filter_edit_mode_objects(objs, active_obj)
            
            preprocess = getattr(self, f"preprocess_{active_obj.type.lower()}", None)
            
            if not preprocess:
                self.report({'ERROR'}, f"Copying for {active_obj.type} is not implemented!")
                return {'CANCELLED'}
            
            if self.mode == 'COPY_LINKED': self.mode = 'COPY'
        
        if not objs:
            self.report({'WARNING'}, f"Nothing to copy")
            return {'CANCELLED'}
        
        # These are used in the preprocess methods and in write_clipboard
        self.context = context
        self.objs = objs
        self.active_obj = active_obj
        
        json_data = self.write_clipboard(preprocess)
        
        context.window_manager.clipboard = json.dumps(json_data, separators=(',',':'))
        
        self.report({'INFO'}, f"Copied {len(objs)} object(s)")
        
        return {'FINISHED'}
    
    def write_clipboard(self, preprocess):
        context = self.context
        scene = context.scene
        objs = self.objs
        active_obj = self.active_obj
        
        self.library_paths = []
        self.library_ids = {}
        
        # Make sure that current file is always the first in the list of libraries
        ClipboardUtil.get_library_id(self.library_paths, self.library_ids, None)
        
        json_data = {
            "content": ClipboardUtil.content_key,
            "context": '3D',
            "mode": self.mode,
            "cursor": ClipboardUtil.serialize_matrix(scene.cursor.matrix),
            "view": ClipboardUtil.serialize_view_rotation(context),
            "frame": scene.frame_current,
            "libraries": (self.library_paths if self.mode != 'COPY_TEXT' else None),
            "active": (self.get_object_id(active_obj) if active_obj in objs else None),
            "hierarchy": self.get_hierarchy_info(objs),
        }
        
        if self.mode != 'COPY_TEXT':
            json_data["link"] = ClipboardUtil.get_link_info(objs, self.library_paths, self.library_ids)
        
        if self.mode != 'COPY_LINKED':
            id_mapper = (lambda obj: self.get_object_id(obj) if isinstance(obj, bpy.types.Object) else "")
            id_map = {}
            
            pack = (self.mode == 'COPY_TEXT')
            json_data["data"] = ClipboardUtil.write(ClipboardUtil.name, objs, pack, preprocess, id_mapper, id_map)
            
            active_id = json_data["active"]
            hierarchy = json_data["hierarchy"]
            
            for src_id, dst_id in id_map.items():
                if src_id == active_id:
                    active_id = dst_id
                    json_data["active"] = dst_id
                
                if src_id in hierarchy:
                    hierarchy[dst_id] = hierarchy[src_id]
                    del hierarchy[src_id]
        
        return json_data
    
    def get_object_id(self, obj):
        if not obj: return ""
        library_id = ClipboardUtil.get_library_id(self.library_paths, self.library_ids, obj)
        return f"{library_id}/{obj.name}"
    
    def get_hierarchy_info(self, objs):
        child_map, parent_map = BlUtil.Object.map_children(objs)
        hierarchy_info = {}
        for obj in objs:
            obj_id = self.get_object_id(obj)
            parent_id = self.get_object_id(parent_map.get(obj))
            matrix = ClipboardUtil.serialize_matrix(obj.matrix_world)
            hierarchy_info[obj_id] = dict(parent=parent_id, parent_bone=obj.parent_bone, matrix=matrix)
        return hierarchy_info
    
    def update_edit_objects(self):
        for obj in self.objs:
            obj.update_from_editmode()
    
    def preprocess_mesh(self):
        bpy.ops.mesh.select_all(action='INVERT')
        bpy.ops.mesh.delete(type=ClipboardUtil.get_mesh_delete_type(self.context))
        bpy.ops.mesh.select_all(action='SELECT')
        self.update_edit_objects()
    
    def preprocess_curve(self):
        bpy.ops.curve.select_all(action='INVERT')
        bpy.ops.curve.delete(type='VERT')
        bpy.ops.curve.select_all(action='SELECT')
        self.update_edit_objects()
    
    def preprocess_surface(self):
        bpy.ops.curve.select_all(action='INVERT')
        bpy.ops.curve.delete(type='VERT')
        bpy.ops.curve.select_all(action='SELECT')
        self.update_edit_objects()
    
    def preprocess_meta(self):
        bpy.ops.mball.select_all(action='INVERT')
        bpy.ops.mball.delete_metaelems()
        bpy.ops.mball.select_all(action='SELECT')
        self.update_edit_objects()
    
    def preprocess_gpencil(self):
        for obj in self.objs:
            gpencil = obj.data
            for layer in gpencil.layers:
                exclude = []
                for frame in layer.frames:
                    if frame == layer.active_frame: continue
                    if gpencil.use_multiedit and frame.select: continue
                    exclude.append(frame)
                
                for frame in exclude:
                    layer.frames.remove(frame)
        
        bpy.ops.gpencil.select_all(action='INVERT')
        bpy.ops.gpencil.delete(type='POINTS')
        bpy.ops.gpencil.select_all(action='SELECT')
        self.update_edit_objects()
    
    def preprocess_armature(self):
        bpy.ops.armature.select_all(action='INVERT')
        bpy.ops.armature.delete()
        bpy.ops.armature.select_all(action='SELECT')
        self.update_edit_objects()

@addon.Operator(idname="view3d.paste", label="Paste", description="Paste objects/elements", options={'REGISTER', 'UNDO'})
class OperatorPaste:
    mode: 'PASTE' | prop("Mode", "Paste mode", items=[
        ('PASTE', "Append", "Objects will be appended"),
        ('PASTE_LINKED', "Link", "Objects will be linked"),
    ])
    
    target_collection: 'ACTIVE' | prop("Collection", "In which collection to put the pasted objects", items=[
        ('SCENE', "Scene", "Put objects directly into the current scene's collection"),
        ('FIRST', "First", "Put objects into the first sub-collection of the current scene"),
        ('ACTIVE', "Active", "Put objects into the same collection(s) as the active object (if these collections are in the current scene)"),
        ('NEW', "New", "Put objects into a new collection"),
    ])
    
    at_cursor: False | prop("At Cursor", "Paste at 3D cursor")
    link_relative: True | prop("Link Relative", "When linking, use relative paths")
    select_pasted: True | prop("Select", "Select the pasted objects/elements")
    replace_selection: False | prop("Replace", "Replace the currently selected objects/elements")
    reuse: False | prop("Reuse", "Try to use the existing materials/textures/images where possible")
    use_frame_offset: True | prop("Frame Offset", "Offset the pasted Grease Pencil frames to the current frame")
    
    reuse_names = ["images", "textures", "materials"]
    
    @classmethod
    def poll(cls, context):
        return context.mode in addon.preferences.modes
    
    def invoke(self, context, event):
        self.mouse_coord = Vector((event.mouse_region_x, event.mouse_region_y))
        return self.execute(context)
    
    def execute(self, context):
        self.context = context
        scene = context.scene
        view_layer = context.view_layer
        active_obj = context.active_object
        
        self.context_mode = context.mode
        self.obj_type = (active_obj.type if active_obj else None)
        self.active_obj_original = active_obj
        self.frame_current = scene.frame_current # also used in gpencil convert
        self.frame_start = scene.frame_start # used in gpencil convert
        
        if self.context_mode != 'OBJECT':
            self.mode = 'PASTE' # make sure it's not PASTE_LINKED
            
            obj_type_low = self.obj_type.lower()
            preprocess = getattr(self, f"preprocess_{obj_type_low}", None)
            convert = getattr(self, f"convert_{obj_type_low}", None)
            postprocess = getattr(self, f"postprocess_{obj_type_low}", self.postprocess)
            
            if not preprocess:
                self.report({'ERROR'}, f"Pasting for {BlEnums.get_mode_name(context.mode)} mode is not implemented!")
                return {'CANCELLED'}
        else:
            preprocess = self.preprocess_object
            convert = None
            postprocess = None
        
        try:
            clipboard = self.read_clipboard(context)
        except (TypeError, KeyError, ValueError, AssertionError) as exc:
            print(traceback.format_exc())
            self.report({'WARNING'}, "Incompatible format of clipboard data")
            return {'CANCELLED'}
        
        active_obj_collections = None
        if (self.target_collection == 'ACTIVE') and active_obj:
            # Note: collections don't support object lookup, so to check for
            # presence we need to convert into a normal python container
            child_collections = BlUtil.Collection.all_children(scene.collection)
            active_obj_collections = [collection for collection in child_collections
                if active_obj in set(collection.objects)]
        
        # Do this before preprocess()
        self.select_pasted_original = self.select_pasted
        self.select_pasted |= (self.at_cursor and (self.context_mode == 'OBJECT'))
        selected_objs = list(context.selected_objects)
        
        preprocess()
        
        if self.context_mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
            bpy.ops.object.select_all(action='DESELECT')
        
        if self.reuse:
            old_resources = {data_name: set(getattr(bpy.data, data_name)) for data_name in self.reuse_names}
        
        with BlUtil.Data.OrphanCleanup(use_fake_user=False, check='NEW'):
            objs = []
            obj_map = {}
            linked = False
            
            if (self.mode == 'PASTE_LINKED') and (clipboard["link"] is not None):
                linked = True
                self.load_link(clipboard, objs, obj_map, link=True, relative=self.link_relative)
            elif (clipboard["mode"] == 'COPY_LINKED') and (clipboard["link"] is not None):
                self.load_link(clipboard, objs, obj_map, link=False)
            else:
                self.load_path(clipboard, objs, obj_map)
            
            if not objs:
                self.report({'WARNING'}, "0 objects pasted")
                return {'CANCELLED'}
            
            if self.target_collection == 'NEW':
                collection = bpy.data.collections.new("Pasted objects")
                scene.collection.children.link(collection)
                collections = [collection]
            elif self.target_collection == 'SCENE':
                collections = [scene.collection]
            elif active_obj_collections:
                collections = active_obj_collections
            else:
                collections = [next(iter(scene.collection.children), scene.collection)]
            
            obj_selection_state = self.select_pasted or (self.context_mode != 'OBJECT')
            frame_offset = self.frame_current - (clipboard["frame"] or 0)
            
            for obj in objs:
                for collection in collections:
                    try:
                        collection.objects.link(obj)
                    except RuntimeError:
                        pass # e.g. if this object is already linked
                
                if self.use_frame_offset: self.offset_frames(obj, frame_offset)
                
                BlUtil.Object.select_set(obj, obj_selection_state, view_layer=view_layer)
            
            if self.select_pasted and (self.context_mode == 'OBJECT'):
                active_obj = obj_map.get(clipboard["active"])
                if active_obj: BlUtil.Object.active_set(active_obj, view_layer=view_layer)
            
            if not linked:
                hierarchy = clipboard["hierarchy"]
                
                # Restore parent relations (Blender actually adds a relationship
                # if both parent and child are imported, but for some reason
                # the imported parent doesn't affect the imported children.
                # Also, on re-parenting the matrix has to be restored.)
                for obj_key, obj in obj_map.items():
                    if not obj: continue
                    
                    hierarchy_info = hierarchy.get(obj_key)
                    if not hierarchy_info: continue
                    
                    parent = obj_map.get(hierarchy_info["parent"])
                    if parent:
                        obj.parent = parent
                        obj.parent_bone = hierarchy_info["parent_bone"] or ""
                    
                    obj.matrix_world = hierarchy_info["matrix"]
            
            if self.at_cursor: bpy.ops.view3d.snap_selected_to_cursor(use_offset=True)
            
            if self.reuse:
                new_resources = {data_name: {idblock for idblock in getattr(bpy.data, data_name)
                    if idblock not in idblocks} for data_name, idblocks in old_resources.items()}
                self.merge_matching_resources(old_resources, new_resources)
            
            if self.context_mode != 'OBJECT':
                if convert:
                    # Blender's conversion operator requires that
                    # one of the selected objects must be active
                    BlUtil.Object.active_set(objs[0], view_layer=view_layer)
                    convert()
                
                # Conversion may delete (replace) some of the original objects,
                # so we have to use bpy.context.selected_objects
                objs = self.remove_unconverted_objs(bpy.context.selected_objects, self.obj_type)
                self.objs = objs
                
                BlUtil.Object.active_set(self.active_obj_original, view_layer=view_layer)
                BlUtil.Object.select_set(self.active_obj_original, True, view_layer=view_layer)
                
                postprocess()
                
                for obj in selected_objs:
                    BlUtil.Object.select_set(obj, True, view_layer=view_layer)
                
                bpy.ops.object.mode_set(mode=BlEnums.mode_to_object(self.context_mode))
            elif self.select_pasted and not self.select_pasted_original:
                for obj in objs:
                    BlUtil.Object.select_set(obj, False, view_layer=view_layer)
                
                for obj in selected_objs:
                    BlUtil.Object.select_set(obj, True, view_layer=view_layer)
                
                # "or None" is to avoid referencing potentially deleted object
                BlUtil.Object.active_set(self.active_obj_original or None, view_layer=view_layer)
        
        # Revert to the original value, for the operator redo panel
        self.select_pasted = self.select_pasted_original
        
        self.report({'INFO'}, f"Pasted {len(objs)} object(s)")
        
        return {'FINISHED'}
    
    def offset_frames(self, obj, frame_delta):
        if obj.type != 'GPENCIL': return
        for layer in obj.data.layers:
            for frame in layer.frames:
                frame.frame_number += frame_delta
    
    def preprocess_object(self):
        if self.replace_selection: bpy.ops.object.delete()
        if self.select_pasted: bpy.ops.object.select_all(action='DESELECT')
    
    def preprocess_mesh(self):
        if self.replace_selection: bpy.ops.mesh.delete(type=ClipboardUtil.get_mesh_delete_type(self.context))
        if self.select_pasted: bpy.ops.mesh.select_all(action='DESELECT')
    
    def preprocess_curve(self):
        if self.replace_selection: bpy.ops.curve.delete(type='VERT')
        if self.select_pasted: bpy.ops.curve.select_all(action='DESELECT')
    
    def preprocess_surface(self):
        self.preprocess_curve()
    
    def preprocess_meta(self):
        if self.replace_selection: bpy.ops.mball.delete_metaelems()
        if self.select_pasted: bpy.ops.mball.select_all(action='DESELECT')
    
    def preprocess_gpencil(self):
        if self.replace_selection: bpy.ops.gpencil.delete(type='POINTS')
        if self.select_pasted: bpy.ops.gpencil.select_all(action='DESELECT')
    
    def preprocess_armature(self):
        if self.replace_selection: bpy.ops.armature.delete()
        if self.select_pasted: bpy.ops.armature.select_all(action='DESELECT')
    
    def remove_unconverted_objs(self, objs, obj_type):
        remaining_objs = []
        for obj in objs:
            if obj.type == obj_type:
                remaining_objs.append(obj)
            else:
                bpy.data.objects.remove(obj)
        return remaining_objs
    
    def convert_mesh(self):
        bpy.ops.object.convert(target='MESH', keep_original=False)
    
    def convert_curve(self):
        # This only works for Text objects
        bpy.ops.object.convert(target='CURVE', keep_original=False)
        
        context = bpy.context
        view_layer = context.view_layer
        scene_collection_objs = context.scene.collection.objects
        
        objs = [obj for obj in context.selected_objects if obj.type != 'CURVE']
        
        depsgraph = context.evaluated_depsgraph_get()
        
        mesh = MeshEquivalent.bake("Bake Mesh", objs, depsgraph)
        verts = [Vector(v.co) for v in mesh.vertices]
        edges = [tuple(e.vertices) for e in mesh.edges]
        polys = [(tuple(p.vertices), p.material_index) for p in mesh.polygons]
        
        curve = bpy.data.curves.new("Curve Convert", type='CURVE')
        curve_obj = bpy.data.objects.new("Curve Convert", curve)
        scene_collection_objs.link(curve_obj)
        BlUtil.Object.select_set(curve_obj, True, view_layer=view_layer)
        
        curve.dimensions = '3D'
        curve.resolution_u = 1
        curve.resolution_v = 1
        
        # Note: curve.splines.new() creates a spline with 1 point
        # already added, so we actually need to add N-1 points.
        
        def get_edge_key(vi0, vi1):
            return ((vi0, vi1) if vi0 < vi1 else (vi1, vi0))
        
        poly_edges = set()
        def add_poly_edge(poly, i):
            i2 = (i - 1 if i > 0 else len(poly) - 1)
            poly_edges.add(get_edge_key(poly[i], poly[i2]))
        
        for poly, material_index in polys:
            spline = curve.splines.new(type='POLY')
            spline.use_cyclic_u = True
            spline.material_index = material_index
            points = spline.points
            points.add(len(poly) - 1)
            for i, vi in enumerate(poly):
                points[i].co = verts[vi].to_4d()
                add_poly_edge(poly, i)
        
        vert_neighbors = {}
        for edge in edges:
            vi0, vi1 = edge
            if get_edge_key(vi0, vi1) in poly_edges: continue
            get_or_add(vert_neighbors, vi0, set).add(vi1)
            get_or_add(vert_neighbors, vi1, set).add(vi0)
        
        def other_vert(neighbors, vi):
            for vi2 in neighbors:
                if vi2 != vi: return vi2
        
        edge_flags = {}
        for start_vi, start_neighbors in vert_neighbors.items():
            neighbor_count = len(start_neighbors)
            if (neighbor_count < 3) and (neighbor_count != 1): continue
            
            for vi1 in start_neighbors:
                vi0 = start_vi
                edge_key = get_edge_key(vi0, vi1)
                if edge_flags.get(edge_key): continue
                
                polyline = [vi0, vi1]
                edge_flags[edge_key] = True
                
                next_neighbors = vert_neighbors[vi1]
                while len(next_neighbors) == 2:
                    vi0, vi1 = vi1, other_vert(next_neighbors, vi0)
                    polyline.append(vi1)
                    next_neighbors = vert_neighbors[vi1]
                
                spline = curve.splines.new(type='POLY')
                spline.use_cyclic_u = False
                points = spline.points
                points.add(len(polyline) - 1)
                for i, vi in enumerate(polyline):
                    points[i].co = verts[vi].to_4d()
        
        bpy.data.meshes.remove(mesh)
    
    def convert_gpencil(self):
        context = bpy.context
        view_layer = context.view_layer
        active_obj = context.active_object
        selected_objs = set(context.selected_objects)
        
        gpencil_objs = {obj for obj in selected_objs if obj.type == 'GPENCIL'}
        non_gpencil_objs = selected_objs - gpencil_objs
        convertible = {obj for obj in non_gpencil_objs if BlEnums.is_convertible(obj.type, 'GPENCIL')}
        not_directly_convertible = {obj for obj in non_gpencil_objs
            if (obj not in convertible) and BlEnums.is_convertible(obj.type, 'MESH')}
        
        # Conversion may remove the original objects, and may not preserve the original names
        
        if not_directly_convertible:
            unselected_objs = set(bpy.data.objects) - selected_objs
            
            BlUtil.Object.select_activate(not_directly_convertible, 'SOLO', active='ANY', view_layer=view_layer)
            bpy.ops.object.convert(target='MESH', keep_original=False)
            
            selected_objs = set(bpy.data.objects) - unselected_objs
            
            convertible = {obj for obj in selected_objs
                if (obj.type != 'GPENCIL') and BlEnums.is_convertible(obj.type, 'GPENCIL')}
        
        if not convertible: return
        
        all_objs = set(bpy.data.objects)
        unselected_objs = all_objs - selected_objs
        other_objs = all_objs - convertible
        
        BlUtil.Object.select_activate(convertible, 'SOLO', active='ANY', view_layer=view_layer)
        bpy.ops.object.convert(target='GPENCIL', keep_original=False)
        
        all_objs = set(bpy.data.objects)
        selected_objs = all_objs - unselected_objs
        convertible = all_objs - other_objs
        
        BlUtil.Object.select_activate(selected_objs, 'SOLO', active='ANY', view_layer=view_layer)
        
        # By default, the conversion operator puts the strokes on the current frame.
        # So we only may need to un-offset them (if use_frame_offset is disabled).
        if not self.use_frame_offset:
            frame_offset = self.frame_start - self.frame_current
            for obj in convertible:
                self.offset_frames(obj, frame_offset)
    
    def postprocess(self):
        bpy.ops.object.join()
    
    def postprocess_mesh(self):
        # Note: for deselecting, we need to clear the selection state of *all* elements
        selection_state = self.select_pasted_original
        for obj in self.objs:
            mesh = obj.data
            for v in mesh.vertices: v.select = selection_state
            for e in mesh.edges: e.select = selection_state
            for p in mesh.polygons: p.select = selection_state
        
        bpy.ops.object.join()
    
    def postprocess_curve(self):
        selection_state = self.select_pasted_original
        for obj in self.objs:
            curve = obj.data
            for spline in curve.splines:
                if spline.type == 'BEZIER':
                    for p in spline.bezier_points:
                        p.select_control_point = selection_state
                        p.select_left_handle = selection_state
                        p.select_right_handle = selection_state
                else:
                    for p in spline.points:
                        p.select = selection_state
        
        # At least as of Blender 2.92, joined curves do not add their materials
        # (and even if the same materials are present in the target object,
        # material indices are not assigned to match those materials)
        bpy.ops.object.join()
    
    def postprocess_surface(self):
        self.postprocess_curve()
    
    def postprocess_meta(self):
        exclude_prop_names = {"type", "select"}
        
        context = bpy.context
        view_layer = context.view_layer
        active_obj = context.active_object
        
        matrix = active_obj.matrix_world.inverted_safe()
        
        selection_state = self.select_pasted_original
        
        active_metaball = active_obj.data
        
        for obj in self.objs:
            metaball = self.ensure_unique_data(obj)
            
            metaball.transform(matrix @ obj.matrix_world)
            
            for element in metaball.elements:
                new_element = active_metaball.elements.new(type=element.type)
                new_element.select = selection_state
                
                for name, rna_prop in BlRna.properties(element):
                    if name in exclude_prop_names: continue
                    setattr(new_element, name, getattr(element, name))
        
        BlUtil.Object.select_set(active_obj, False, view_layer=view_layer)
        bpy.ops.object.delete()
        BlUtil.Object.select_set(active_obj, True, view_layer=view_layer)
    
    def postprocess_gpencil(self):
        # For Gpencil, it is required that all rotations are applied before joining
        context = bpy.context
        view_layer = context.view_layer
        scene_collection_objs = context.scene.collection.objects
        active_obj = context.active_object
        
        # We copy everything to avoid interference from parents/constraints/drivers
        def make_copy(obj, unique=True):
            data = (self.ensure_unique_data(obj) if unique else obj.data)
            new_obj = bpy.data.objects.new(obj.name, data)
            scene_collection_objs.link(new_obj)
            BlUtil.Object.select_set(new_obj, True, view_layer=view_layer)
            return new_obj
        
        bpy.ops.object.select_all(action='DESELECT')
        
        target_obj = make_copy(active_obj, False)
        target_matrix = active_obj.matrix_world.inverted_safe()
        
        source_objs_matrices = [(make_copy(obj), obj.matrix_world) for obj in self.objs]
        
        selection_state = self.select_pasted_original
        
        for obj, obj_matrix in source_objs_matrices:
            gpencil = obj.data
            matrix = target_matrix @ obj_matrix
            for layer in gpencil.layers:
                for frame in layer.frames:
                    for stroke in frame.strokes:
                        for point in stroke.points:
                            point.co = matrix @ point.co
                            point.select = selection_state
        
        BlUtil.Object.active_set(target_obj, view_layer=view_layer)
        
        bpy.ops.object.join()
        
        for obj in self.objs:
            BlUtil.Object.select_set(obj, True, view_layer=view_layer)
        
        bpy.ops.object.delete()
        
        BlUtil.Object.active_set(active_obj, view_layer=view_layer)
    
    def postprocess_armature(self):
        selection_state = self.select_pasted_original
        for obj in self.objs:
            armature = obj.data
            for bone in armature.bones:
                bone.select = selection_state
                bone.select_head = selection_state
                bone.select_tail = selection_state
        
        bpy.ops.object.join()
    
    def ensure_unique_data(self, obj):
        data = obj.data
        if data.users > 1 + int(data.use_fake_user):
            data = data.copy()
            obj.data = data
        return data
    
    def merge_matching_resources(self, old_resources, new_resources):
        ignore = set(bpy.types.ID.bl_rna.properties.keys())
        ignore_image = {"bindcode"} | ignore
        ignore_image_nopixels = {"pixels"} | ignore_image
        
        def compare_pixels(rna_prop, valueA, valueB):
            return valueA[:] == valueB[:]
        
        def should_check_pixels(img):
            if img.source == 'GENERATED': return True
            if img.source == 'TILED': return (not img.filepath) or img.is_dirty
            return False
        
        def compare_image(itemA, itemB):
            if should_check_pixels(itemA) and should_check_pixels(itemB):
                return BlRna.compare(itemA, itemB, ignore=ignore_image, specials={"pixels":compare_pixels})
            return BlRna.compare(itemA, itemB, ignore=ignore_image_nopixels)
        
        def compare_node_tree(rna_prop, valueA, valueB):
            return NodeTreeComparer.compare(valueA, valueB)
        
        def compare_shadeable(itemA, itemB):
            return BlRna.compare(itemA, itemB, ignore=ignore, specials={"node_tree":compare_node_tree})
        
        comparers = {"images":compare_image, "textures":compare_shadeable, "materials":compare_shadeable}
        
        for data_name in self.reuse_names:
            comparer = comparers[data_name]
            old_idblocks = old_resources[data_name]
            new_idblocks = new_resources[data_name]
            
            for new_idblock in new_idblocks:
                self.merge_matching_resource(data_name, new_idblock, old_idblocks, comparer)
    
    def merge_matching_resource(self, data_name, new_idblock, old_idblocks, comparer):
        for old_idblock in old_idblocks:
            if not comparer(old_idblock, new_idblock): continue
            
            count0 = new_idblock.users - int(new_idblock.use_fake_user)
            
            new_idblock.user_remap(old_idblock)
            
            count1 = new_idblock.users - int(new_idblock.use_fake_user)
            
            if new_idblock.users <= int(new_idblock.use_fake_user):
                getattr(bpy.data, data_name).remove(new_idblock)
            
            return
    
    def add_datablocks(self, data_from, data_to, data_name, names):
        if not hasattr(data_from, data_name): return []
        names = set(names)
        names.intersection_update(getattr(data_from, data_name, ()))
        names = sorted(names)
        setattr(data_to, data_name, list(names)) # IMPORTANT: assign a copy
        return names
    
    def load_link(self, clipboard, objs, obj_map, link, relative=True):
        libraries = clipboard["libraries"]
        
        # Note: if we try to link from the current file, Blendr will crash
        current_path = ClipboardUtil.normpath(bpy.data.filepath)
        
        obj_keys = set()
        
        for lib_id, data_infos in clipboard["link"].items():
            path = ClipboardUtil.normpath(libraries[lib_id])
            
            if (not os.path.isfile(path)) or (path == current_path):
                obj_names = data_infos.get("objects")
                if obj_names: obj_keys.update((lib_id, name) for name in obj_names)
                continue
            
            obj_names = None
            
            abs_path = path
            
            if relative: path = bpy.path.relpath(path)
            
            # Blend file's libriaries are not affected by Undo/Redo, so if the user
            # toggles the link_relative property after paste, it won't have any effect.
            # To mitigate this, we treat libraries without any actual references
            # as "added by Paste operation" and modify their path explicitly/manually.
            for library in bpy.data.libraries:
                if ClipboardUtil.normpath(library.filepath) != abs_path: continue
                if library.filepath == path: continue
                if len(library.users_id) > 0: continue
                library.filepath = path
            
            with bpy.data.libraries.load(path, link=link, relative=relative) as (data_from, data_to):
                for data_name, names in data_infos.items():
                    names = self.add_datablocks(data_from, data_to, data_name, names)
                    if data_name == "objects": obj_names = names
            
            if obj_names:
                objs.extend(obj for obj in data_to.objects if obj)
                obj_map.update(((lib_id, name), obj) for name, obj in zip(obj_names, data_to.objects))
        
        if obj_keys: self.load_path(clipboard, objs, obj_map, obj_keys)
    
    def load_path(self, clipboard, objs, obj_map, obj_keys=None):
        path = ClipboardUtil.normpath(clipboard["path"])
        if not os.path.isfile(path): return
        
        if obj_keys is None: obj_keys = clipboard["hierarchy"].keys()
        
        name_ids = {}
        for lib_id, name in obj_keys:
            if (name not in name_ids) or (lib_id == 0): name_ids[name] = lib_id
        
        with bpy.data.libraries.load(path, link=False) as (data_from, data_to):
            names = self.add_datablocks(data_from, data_to, "objects", name_ids.keys())
        
        # Blender adds to libraries even when something is appended,
        # but a reference to a temporary clipboard file only creates clutter
        if hasattr(bpy.data.libraries, "remove"): # available since Blender 2.90
            for library in bpy.data.libraries:
                if ClipboardUtil.normpath(library.filepath) != path: continue
                bpy.data.libraries.remove(library)
                break
        
        objs.extend(obj for obj in data_to.objects if obj)
        obj_map.update(((name_ids[name], name), obj) for name, obj in zip(names, data_to.objects))
    
    def read_clipboard(self, context):
        def get_convert(d, name, converter):
            value = d.get(name)
            return (None if value is None else converter(value))
        
        def get_value(d, name, type):
            value = d.get(name)
            assert isinstance(value, type) or (value is None)
            return value
        
        json_data = json.loads(context.window_manager.clipboard)
        
        assert json_data["content"] == ClipboardUtil.content_key
        
        clipboard = {
            "context": get_value(json_data, "context", str),
            "mode": get_value(json_data, "mode", str),
            "cursor": ClipboardUtil.deserialize_matrix(json_data.get("cursor")),
            "view": get_convert(json_data, "view", Quaternion),
            "frame": get_convert(json_data, "frame", int),
            "libraries": get_value(json_data, "libraries", list) or [""],
        }
        
        for lib_path in clipboard["libraries"]:
            assert isinstance(lib_path, str)
        
        lib_count = len(clipboard["libraries"])
        
        def to_ref(value):
            if not value: return None # empty string is also possible
            lib_id, name = value.split("/", 1)
            lib_id = int(lib_id)
            assert (lib_id >= 0) and (lib_id < lib_count)
            return (lib_id, name)
        
        clipboard["active"] = to_ref(get_value(json_data, "active", str))
        
        clipboard["hierarchy"] = {
            to_ref(obj_key): {
                "parent": to_ref(get_value(obj_info, "parent", str)),
                "parent_bone": get_value(obj_info, "parent_bone", str) or "",
                "matrix": ClipboardUtil.deserialize_matrix(obj_info.get("matrix")),
            }
            for obj_key, obj_info in get_value(json_data, "hierarchy", dict).items()
        }
        
        clipboard["link"] = get_value(json_data, "link", dict)
        
        if clipboard["link"] is not None:
            def validate_lib_data(d):
                assert isinstance(d, dict)
                for value in d.values():
                    assert isinstance(value, list)
                    for item in value:
                        assert isinstance(item, str)
                return d
            
            clipboard["link"] = {int(key): validate_lib_data(value) for key, value in clipboard["link"].items()}
        
        clipboard["path"] = ClipboardUtil.get_clipboard_path(ClipboardUtil.name)
        
        data = get_value(json_data, "data", str)
        if data: ClipboardUtil.write_b64(clipboard["path"], data)
        
        return clipboard

@addon.Operator(idname="view3d.cut", label="Cut", description="Cut objects/elements")
class OperatorCut:
    @classmethod
    def poll(cls, context):
        return bpy.ops.view3d.copy.poll()
    
    def execute(self, context):
        bpy.ops.view3d.copy(mode='COPY')
        
        objs = set(context.selected_objects)
        active_obj = context.active_object
        
        if context.mode != 'OBJECT':
            objs = ClipboardUtil.filter_edit_mode_objects(objs, active_obj)
            process = getattr(self, f"process_{active_obj.type.lower()}", None)
        else:
            process = self.process_object
        
        # These are used by the process() methods
        self.context = context
        self.objs = objs
        self.active_obj = active_obj
        
        process()
        
        bpy.ops.ed.undo_push(message="Cut")
        
        context.area.tag_redraw()
        
        return {'FINISHED'}
    
    def update_edit_objects(self):
        for obj in self.objs:
            obj.update_from_editmode()
    
    def process_object(self):
        bpy.ops.object.delete()
    
    def process_mesh(self):
        bpy.ops.mesh.delete(type=ClipboardUtil.get_mesh_delete_type(self.context))
        self.update_edit_objects()
    
    def process_curve(self):
        bpy.ops.curve.delete(type='VERT')
        self.update_edit_objects()
    
    def process_surface(self):
        bpy.ops.curve.delete(type='VERT')
        self.update_edit_objects()
    
    def process_meta(self):
        bpy.ops.mball.delete_metaelems()
        self.update_edit_objects()
    
    def process_gpencil(self):
        bpy.ops.gpencil.delete(type='POINTS')
        self.update_edit_objects()
    
    def process_armature(self):
        bpy.ops.armature.delete()
        self.update_edit_objects()

@addon.PropertyGroup
class ShortcutInfo:
    op_type: 'COPY' | addon.prop_keymap("Operation", "Operation type", items=[
        ('COPY', "Copy", "Copy (write a temporary file)"),
        ('COPY_TEXT', "Copy as Text", "Copy (store all data in a text clipboard)"),
        ('COPY_LINKED', "Copy Linked", "Copy (don't write a temporary file if data can be linked)"),
        ('PASTE', "Paste", "Paste (objects will be appended)"),
        ('PASTE_LINKED', "Paste Linked", "Paste (objects will be linked)"),
        ('CUT', "Cut", "Cut (write a temporary file)"),
    ])
    
    key: 'NONE' | addon.prop_keymap_key("Key", "Key")
    value: 'PRESS' | addon.prop_keymap_value("Event", "Event")
    mods: {} | addon.prop_keymap_mods("Modifiers", "Modifiers")
    
    def setup(self, mods, key, op_type, value='PRESS'):
        self.mods = mods
        self.key = key
        self.value = value
        self.op_type = op_type

@addon.Preferences.Include
class AddonPreferences:
    shortcuts: [ShortcutInfo] | prop("Shortcuts")
    
    def _get_shortcuts_count(self):
        shortcuts = addon.preferences.shortcuts
        return len(shortcuts)
    def _set_shortcuts_count(self, value):
        shortcuts = addon.preferences.shortcuts
        while len(shortcuts) > value: shortcuts.remove(len(shortcuts) - 1)
        while len(shortcuts) < value: shortcuts.add()
        addon.update_keymaps()
    shortcuts_count: 0 | prop("Shortcuts Count", "Number of auto-registered shortcuts",
        get=_get_shortcuts_count, set=_set_shortcuts_count, min=0, max=32)
    
    modes: ClipboardUtil.modes | prop("Modes", "Object modes", items=ClipboardUtil.modes_items)
    
    def actual_coordsystem(self, context=None):
        if self.coordinate_system == 'CONTEXT':
            is_edit = ('EDIT' in (context or bpy.context).mode)
            return ('LOCAL' if is_edit else 'GLOBAL')
        return self.coordinate_system
    
    def draw(self, context):
        layout = NestedLayout(self.layout)
        
        with layout.row(align=True):
            layout.label(text="Modes:")
            layout.prop_enum_filtered(self, "modes")
        
        with layout.row(align=True):
            layout.label(text="Auto-registered shortcuts:")
            layout.prop(self, "shortcuts_count", text="Count")
        
        col = layout.column(align=True)
        for shortcut in self.shortcuts:
            row = col.row(align=True)
            if not shortcut.mods:
                mods_text = "No modifiers"
            elif "any" in shortcut.mods:
                mods_text = "Any modifier"
            else:
                mods_text = " + ".join(mod.capitalize() for mod in sorted(shortcut.mods))
            row.prop_menu_enum(shortcut, "mods", text=mods_text)
            row.prop(shortcut, "key", text="", event=True)
            row.prop(shortcut, "value", text="")
            row.prop(shortcut, "op_type", text="")

@addon.Menu(idname="VIEW3D_MT_cut_copy_paste", label="Copy/Paste")
class VIEW3D_MT_cut_copy_paste:
    def draw(self, context):
        layout = self.layout
        #layout.operator_context = 'INVOKE_DEFAULT' # is this needed?
        
        op_info = layout.operator("view3d.copy", text="Copy")
        op_info.mode = 'COPY'
        
        op_info = layout.operator("view3d.copy", text="Copy as Text")
        op_info.mode = 'COPY_TEXT'
        
        op_info = layout.operator("view3d.copy", text="Copy Linked")
        op_info.mode = 'COPY_LINKED'
        
        op_info = layout.operator("view3d.paste", text="Paste")
        op_info.mode = 'PASTE'
        
        op_info = layout.operator("view3d.paste", text="Paste Linked")
        op_info.mode = 'PASTE_LINKED'
        
        layout.operator("view3d.cut", text="Cut")
    
    @staticmethod
    def register_menus():
        def draw(self, context):
            if context.mode not in addon.preferences.modes: return
            self.layout.separator()
            self.layout.menu("VIEW3D_MT_cut_copy_paste", text="Copy/Paste")
        
        addon.ui_draw("VIEW3D_MT_object_context_menu", 'APPEND')(draw)
        
        for mode in ClipboardUtil.modes:
            type_name = f"VIEW3D_MT_{mode.lower()}"
            if not hasattr(bpy.types, type_name): continue
            addon.ui_draw(type_name, 'APPEND')(draw)

@addon.on_register_keymaps
def register_keymaps():
    if not addon.preferences.is_property_set("shortcuts"):
        with addon.prop_keymap_lock:
            ctrl = ('ctrl' if sys.platform != "darwin" else 'oskey')
            shortcuts = addon.preferences.shortcuts
            shortcuts.add().setup({ctrl}, 'C', 'COPY')
            shortcuts.add().setup({ctrl}, 'C', 'COPY_TEXT', value='DOUBLE_CLICK')
            shortcuts.add().setup({ctrl, 'alt'}, 'C', 'COPY_LINKED')
            shortcuts.add().setup({ctrl}, 'V', 'PASTE')
            shortcuts.add().setup({ctrl, 'alt'}, 'V', 'PASTE_LINKED')
            shortcuts.add().setup({ctrl}, 'X', 'CUT')
            shortcuts.add().setup({ctrl}, 'INSERT', 'COPY')
            shortcuts.add().setup({ctrl}, 'INSERT', 'COPY_TEXT', value='DOUBLE_CLICK')
            shortcuts.add().setup({'shift'}, 'INSERT', 'PASTE')
            shortcuts.add().setup({'alt'}, 'INSERT', 'PASTE_LINKED')
            shortcuts.add().setup({'shift'}, 'DEL', 'CUT')
    
    wm = bpy.context.window_manager
    kc = wm.keyconfigs.addon
    if not kc: return
    
    km = kc.keymaps.new(name="3D View", space_type='VIEW_3D')
    
    for shortcut in addon.preferences.shortcuts:
        km_kwargs = {mod:True for mod in shortcut.mods}
        
        if shortcut.key == 'NONE': continue
        
        if shortcut.op_type.startswith('COPY'):
            kmi = km.keymap_items.new("view3d.copy", shortcut.key, shortcut.value, **km_kwargs)
            kmi.properties.mode = shortcut.op_type
        elif shortcut.op_type.startswith('PASTE'):
            kmi = km.keymap_items.new("view3d.paste", shortcut.key, shortcut.value, **km_kwargs)
            kmi.properties.mode = shortcut.op_type
        elif shortcut.op_type.startswith('CUT'):
            kmi = km.keymap_items.new("view3d.cut", shortcut.key, shortcut.value, **km_kwargs)

def register():
    addon.register()
    
    VIEW3D_MT_cut_copy_paste.register_menus()

def unregister():
    addon.unregister()
