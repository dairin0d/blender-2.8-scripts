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

import os
import itertools
import collections
import json
import time
import random
import inspect
import sys
import traceback
import ast
import shutil

import bpy

from mathutils import Vector, Matrix, Quaternion, Euler, Color

from .utils_python import ensure_baseclass, issubclass_safe, add_mixins, AttributeHolder, binary_search, rmtree, os_remove_all
from .utils_text import compress_whitespace, indent, unindent, split_camelcase
from .bpy_inspect import BlRna, BpyProp, BpyOp, prop, enum_memorizer
from .utils_ui import messagebox, NestedLayout, BlUI
from .utils_blender import BpyPath

#============================================================================#

# Some tips for [regression] testing:
# * Enable -> disable -> enable
# * Enabled by default, disabled by default
# * Default scene, scene saved without addon, scene saved with addon
# * Stable Blender, nightly build, GSOC/open-project branch

# TODO: "load/save/import/export config" buttons in addon preferences (draw() method)

# ===== ADDON MANAGER ===== #
class AddonManager:
    _textblock_prefix = "textblock://"
    
    _screen_name = "Default"
    _screen_mark = "\x02{addon-internal-storage}\x03"
    
    # ===== INITIALIZATION ===== #
    def __new__(cls, name=None, path=None, config=None):
        varname = "__<blender-addon>__"
        info = cls.__init_info(name, path, config, varname)
        
        self = info.get("addon")
        if self is not None: return self # one already exists for this addon
        
        self = object.__new__(cls)
        self.status = 'INITIALIZATION'
        
        self.name = info["name"] # displayable name
        self.path = info["path"] # directory of the main file
        self.module_name = info["module_name"]
        self.is_textblock = info["is_textblock"]
        self.storage_path = info["config_path"]
        
        self.classes_new = collections.OrderedDict() # candidate classes
        self.classes = collections.OrderedDict() # registered classes
        self.classes_static = collections.OrderedDict()
        self.objects = {} # type extensions, handlers, etc.
        self.objects_static_info = []
        
        self._preferences = None
        
        self._on_register = []
        self._on_unregister = []
        
        self.__init_config_storages()
        
        self.__gen_registrable_decorators()
        
        info["module_globals"][varname] = self
        
        return self
    
    def __init_config_storages(self):
        name_prefs = f"{self.module_name}-preferences"
        name_external = f"{self.module_name}-external-storage"
        name_internal = f"{self.module_name}-internal-storage"
        name_runtime = f"{self.module_name}-runtime-settings"
        
        self.storage_name_external = f"<{name_external}>"
        self.storage_name_internal = f"<{name_internal}>"
        
        @classmethod
        def Include(cls, decor_cls):
            decor_annotations = getattr(decor_cls, "__annotations__", None)
            if decor_annotations:
                annotations = getattr(cls, "__annotations__", None)
                if annotations is None:
                    annotations = {}
                    cls.__annotations__ = annotations
                annotations.update(decor_annotations)
            
            for name in dir(decor_cls):
                if not (name.startswith("__") and name.endswith("__")):
                    setattr(cls, name, getattr(decor_cls, name))
            
            return cls
        
        self._Preferences = type(name_prefs, (bpy.types.AddonPreferences,), {"Include":Include, "bl_idname":self.module_name})
        self.__add_class(self._Preferences)
        
        self._External = self.PropertyGroup(type(name_external, (), {"Include":Include}))
        self._Internal = self.PropertyGroup(type(name_internal, (), {"Include":Include}))
        
        self._Runtime = type(name_runtime, (AttributeHolder,), {})
        self._runtime = self._Runtime()
    
    # If this is a textblock, its module will be topmost
    # and will have __name__ == "__main__".
    # If this is an addon in the scripts directory, it will
    # be recognized by blender only if it contains bl_info.
    @classmethod
    def __init_info(cls, name, path, config, varname):
        module_globals = None
        module_locals = None
        module_name = None
        
        for frame_record in reversed(inspect.stack()):
            # Frame record is a tuple of 6 elements:
            # (frame_obj, filename, line_id, func_name, context_lines, context_line_id)
            frame = frame_record[0]
            
            if not module_name:
                module_globals = frame.f_globals
                module_locals = frame.f_locals
                module_name = module_globals.get("__name__", "").split(".")[0]
                _path = module_globals.get("__file__", "")
                _name = os.path.splitext(os.path.basename(_path))[0]
            
            info = frame.f_globals.get("bl_info")
            if info:
                module_globals = frame.f_globals
                module_locals = frame.f_locals
                module_name = module_globals.get("__name__", "").split(".")[0]
                _path = module_globals.get("__file__", "")
                _name = info.get("name", _name)
                break
        
        if varname in module_globals:
            addon = module_globals[varname]
            if isinstance(addon, AddonManager):
                if addon.status == 'INITIALIZATION':
                    return dict(addon=addon) # use this addon object
        
        is_textblock = (module_name == "__main__")
        if is_textblock:
            module_name = os.path.splitext(os.path.basename(_path))[0]
            config_path = cls._textblock_prefix
        else:
            config_path = bpy.utils.user_resource('CONFIG', module_name)
        
        name = name or _name
        path = path or _path
        
        if not os.path.isdir(path):
            # directories/single_file_addon.py
            # directories/some_blend.blend/TextBlockName
            path = os.path.dirname(path)
        
        if os.path.isfile(path):
            # directories/some_blend.blend
            path = os.path.dirname(path)
        
        if not os.path.isdir(path):
            # No such directory in the filesystem
            path = cls._textblock_prefix
        
        if not config:
            config = (bpy.path.clean_name(module_name) if is_textblock else "config") + ".json"
        
        config_path = os.path.join(config_path, config)
        
        return dict(name=name, path=path, config_path=config_path,
                    module_name=module_name, is_textblock=is_textblock,
                    module_locals=module_locals, module_globals=module_globals)
    #========================================================================#
    
    # ===== PREFERENCES / EXTERNAL / INTERNAL ===== #
    # Prevent accidental assignment
    Preferences = property(lambda self: self._Preferences)
    External = property(lambda self: self._External)
    Internal = property(lambda self: self._Internal)
    Runtime = property(lambda self: self._Runtime)
    
    preferences = property(lambda self: self._preferences)
    prefs = property(lambda self: self._preferences) # just a shorter alias
    external = property(lambda self: self.external_attr(self.storage_name_external))
    internal = property(lambda self: self.internal_attr(self.storage_name_internal))
    runtime = property(lambda self: self._runtime)
    
    @classmethod
    def external_attr(cls, name):
        wm = bpy.data.window_managers[0]
        return getattr(wm, name, None)
    
    @classmethod
    def internal_attr(cls, name):
        screen = cls._find_internal_storage()
        return getattr(screen, name, None)
    
    @classmethod
    def _find_internal_storage(cls):
        screen = bpy.data.screens.get(cls._screen_name)
        if (not screen) or (not screen.get(cls._screen_mark)):
            for screen in bpy.data.screens:
                if screen.name == "temp": continue
                # When file browser is open, a temporary screen exists with "-nonnormal" suffix
                if screen.name.endswith("-nonnormal"): continue
                cls._screen_name = screen.name
                if screen.get(cls._screen_mark): return screen
            screen = bpy.data.screens.get(cls._screen_name)
            screen[cls._screen_mark] = True
        return screen
    
    def path_resolve(self, path, coerce=True):
        if path.startswith(self.storage_name_external):
            obj = self.external
            path = path[len(self.storage_name_external) + 1:]
        elif path.startswith(self.storage_name_internal):
            obj = self.internal
            path = path[len(self.storage_name_internal) + 1:]
        else:
            raise ValueError(f"Path '{path}' could not be resolved")
        return obj.path_resolve(path, coerce)
    
    def external_load(self):
        obj = self.external
        if not obj: return
        
        path = self.storage_path
        
        if path.startswith(self._textblock_prefix):
            path = path[len(self._textblock_prefix):]
            text_block = bpy.data.texts.get(path)
            if not text_block: return
            text = text_block.as_string()
        else:
            try:
                # Settings are expected to be written by
                # the addon, so it shouldn't be necessary
                # to handle BOM-marks and encodings.
                with open(path, "r") as f:
                    text = f.read()
            except IOError:
                return
        
        try:
            data = json.loads(text)
        except ValueError:
            # Maybe display a warning?
            return
        
        BpyProp.deserialize(obj, data, use_skip_save=True)
    
    def external_save(self):
        obj = self.external
        if not obj: return
        
        data = BpyProp.serialize(obj, use_skip_save=True)
        
        text = json.dumps(data, indent=2)
        
        path = self.storage_path
        
        if path.startswith(self._textblock_prefix):
            path = path[len(self._textblock_prefix):]
            text_block = bpy.data.texts.get(path)
            if not text_block: text_block = bpy.data.texts.new(path)
            text_block.from_string(text)
        else:
            try:
                with open(path, "w") as f:
                    f.write(text)
            except IOError:
                messagebox(f"{self.name}: failed to write config file\n\"{self.storage_path}\"", 'ERROR')
                return
    #========================================================================#
    
    # ===== HANDLERS AND TYPE EXTENSIONS ===== #
    def on_register(self, callback):
        self._on_register.append(callback)
        return callback
    
    def on_unregister(self, callback):
        self._on_unregister.append(callback)
        return callback
    
    @property
    def ContextMenu(self):
        ContextMenu = getattr(bpy.types, "WM_MT_button_context", None)
        if not ContextMenu:
            # https://docs.blender.org/api/blender2.8/bpy.types.Menu.html#extending-the-button-context-menu
            # This class has to be exactly named like that to insert an entry in the right click menu
            # ATTENTION: there can be only one such registered class, so it HAS to be shared between addons
            ContextMenu = type("WM_MT_button_context", (bpy.types.Menu,), dict(bl_label="-", draw=(lambda self, context: None)))
            bpy.utils.register_class(ContextMenu)
        return ContextMenu
    
    def context_menu(self, order, owner=None):
        return self.ui_draw(self.ContextMenu, order, owner)
    
    def ui_draw(self, ui, order, owner=None):
        def decorator(draw_func):
            if order.upper().startswith('PRE'):
                self.ui_prepend(ui, draw_func, owner)
            else:
                self.ui_append(ui, draw_func, owner)
            return draw_func
        return decorator
    
    def type_extension(self, struct, prop_name, owner=None):
        def decorator(pg):
            self.type_extend(struct, prop_name, pg, owner=None)
            return pg
        return decorator
    
    def handler(self, handler_name, persistent=False, owner=None):
        if isinstance(handler_name, str):
            handler_names = (handler_name,)
        else:
            handler_names = handler_name
        def decorator(callback):
            if persistent: callback = bpy.app.handlers.persistent(callback)
            for handler_name in handler_names:
                self.handler_append(handler_name, callback, owner)
            return callback
        return decorator
    
    def draw_handler(self, struct, event, args=(), region='WINDOW', owner=None):
        def decorator(callback):
            self.draw_handler_add(struct, callback, args, region, event, owner)
            return callback
        return decorator
    
    def timer(self, *args, **kwargs):
        if (len(args) == 1) and callable(args[0]):
            self.timer_register(args[0])
            return args[0]
        else:
            first_interval = (args[0] if len(args) > 0 else kwargs.get("first_interval", 0))
            persistent = (args[1] if len(args) > 1 else kwargs.get("persistent", False))
            owner = (args[2] if len(args) > 2 else kwargs.get("owner", None))
            def decorator(callback):
                self.timer_register(callback, first_interval, persistent, owner)
                return callback
            return decorator
    
    def event_timer_add(self, time_step, window=None, owner=None):
        if self.__during_init("add a timer event"):
            self.objects_static_info.append(("event_timer_add", time_step, window, owner))
        else:
            wm = bpy.context.window_manager
            timer = wm.event_timer_add(time_step, window=(window or bpy.context.window))
            
            self.objects[timer] = {
                "key":timer,
                "object":timer,
                "struct":wm,
                "family":'EVENT_TIMER',
                "time_step":time_step,
                "window":window,
                "owner":owner,
            }
            
            return timer
    
    def ui_append(self, ui, draw_func, owner=None): # e.g. menus: bpy.types.*_MT_*.append()
        if isinstance(ui, str): ui = getattr(bpy.types, ui)
        
        if self.__during_init("append to UI"):
            self.objects_static_info.append(("ui_append", ui, draw_func, owner))
        else:
            key = (ui, draw_func)
            
            if key in self.objects: ui.remove(draw_func)
            ui.append(draw_func)
            
            self.objects[key] = {
                "key":key,
                "object":draw_func,
                "struct":ui,
                "family":'UI',
                "mode":'APPEND',
                "callback":draw_func,
                "owner":owner,
            }
    
    def ui_prepend(self, ui, draw_func, owner=None): # e.g. menus: bpy.types.*_MT_*.prepend()
        if isinstance(ui, str): ui = getattr(bpy.types, ui)
        
        if self.__during_init("prepend to UI"):
            self.objects_static_info.append(("ui_prepend", ui, draw_func, owner))
        else:
            key = (ui, draw_func)
            
            if key in self.objects: ui.remove(draw_func)
            ui.prepend(draw_func)
            
            self.objects[key] = {
                "key":key,
                "object":draw_func,
                "struct":ui,
                "family":'UI',
                "mode":'PREPEND',
                "callback":draw_func,
                "owner":owner,
            }
    
    def type_extend(self, struct, prop_name, prop_info, owner=None):
        if isinstance(struct, str): struct = getattr(bpy.types, struct)
        if issubclass_safe(prop_info, bpy.types.PropertyGroup): prop_info = prop_info | prop()
        
        if self.__during_init("extend a type"):
            self.objects_static_info.append(("type_extend", struct, prop_name, prop_info, owner))
        else:
            key = (struct, prop_name)
            
            pg = prop_info[1].get("type")
            if pg and (pg.__name__.endswith(":AUTOREGISTER") or (pg in self.classes_new)):
                self.__register_class(pg)
            
            setattr(struct, prop_name, prop_info)
            
            self.objects[key] = {
                "key":key,
                "object":prop_name,
                "struct":struct,
                "family":'BPY_TYPE',
                "owner":owner,
            }
    
    def handler_append(self, handler_name, callback, owner=None): # see bpy.app.handlers
        if self.__during_init("append a handler"):
            self.objects_static_info.append(("handler_append", handler_name, callback, owner))
        else:
            key = (handler_name, callback)
            
            handlers = getattr(bpy.app.handlers, handler_name)
            if (key in self.objects) and (callback in handlers): handlers.remove(callback)
            handlers.append(callback)
            
            self.objects[key] = {
                "key":key,
                "object":callback,
                "struct":handler_name,
                "family":'HANDLER',
                "callback":callback,
                "owner":owner,
            }
    
    def draw_handler_add(self, struct, callback, args, region, event, owner=None):
        if isinstance(struct, str): struct = getattr(bpy.types, struct)
        
        if self.__during_init("add a draw callback"):
            self.objects_static_info.append(("draw_handler_add", struct, callback, args, region, event, owner))
        else:
            handler = struct.draw_handler_add(callback, args, region, event)
            
            self.objects[handler] = {
                "key":handler,
                "object":handler,
                "struct":struct,
                "family":'DRAW_HANDLER',
                "callback":callback,
                "args":args,
                "region_type":region, # usually WINDOW
                "draw_type":event, # draw_type in API (POST_PIXEL, POST_VIEW, PRE_VIEW, BACKDROP)
                "owner":owner,
            }
            
            return handler
    
    def timer_register(self, callback, first_interval=0, persistent=False, owner=None):
        if self.__during_init("register a timer"):
            self.objects_static_info.append(("timer_register", callback, first_interval, persistent, owner))
        else:
            key = callback
            
            # Note: currently, Blender allows to register the same timer function
            # unlimited number of times, but can unregister only the first one of them
            if bpy.app.timers.is_registered(callback): bpy.app.timers.unregister(callback)
            
            bpy.app.timers.register(callback, first_interval=first_interval, persistent=persistent)
            
            self.objects[key] = {
                "key":key,
                "object":callback,
                "struct":None,
                "family":'TIMER',
                "callback":callback,
                "first_interval":first_interval,
                "persistent":persistent,
                "owner":owner,
            }
    
    def __during_init(self, action):
        if self.status == 'INITIALIZATION': return True
        if self.status not in ('UNREGISTRATION', 'UNREGISTERED'): return False
        raise RuntimeError(f"Attempt to {action} during or after addon unregistration")
    
    def __remove(self, info):
        if not info: return
        family = info["family"]
        if family == 'EVENT_TIMER':
            info["struct"].event_timer_remove(info["object"])
        elif family == 'UI':
            info["struct"].remove(info["object"])
        elif family == 'BPY_TYPE':
            delattr(info["struct"], info["object"])
        elif family == 'HANDLER':
            handlers = getattr(bpy.app.handlers, info["struct"])
            if info["object"] in handlers: handlers.remove(info["object"])
        elif family == 'DRAW_HANDLER':
            info["struct"].draw_handler_remove(info["object"], info["region_type"])
        elif family == 'TIMER':
            if bpy.app.timers.is_registered(info["object"]):
                bpy.app.timers.unregister(info["object"])
        del self.objects[info["key"]]
    
    def remove(self, *keys, **filters):
        if filters.get("all"):
            for info in tuple(self.objects.values()):
                self.__remove(info)
            return
        
        for key in keys:
            info = self.objects.get(key)
            if info: self.__remove(info)
        
        if filters: # Note: all(empty iterable) returns True
            for info in tuple(self.objects.values()):
                if all((k in info) and (info[k] == v) for k, v in filters.items()):
                    self.__remove(info)
    
    #========================================================================#
    
    # ===== REGISTER / UNREGISTER ===== #
    __prop_callbacks = ("items", "update", "get", "set", "poll")
    def __register_class_props(self, cls, parents=()):
        prop_infos = dict(BpyProp.iterate(cls, inherited=True))
        
        parents = list(parents)
        parents.append(cls)
        
        for key, info in prop_infos.items():
            # Do some autocompletion on property descriptors
            if "name" not in info: info["name"] = bpy.path.display_name(key)
            if "description" not in info: info["description"] = info["name"]
            
            # This is syntactic sugar for the case when callbacks
            # are defined later than the properties which use them.
            for callback_name in self.__prop_callbacks:
                callback = info.get(callback_name)
                if isinstance(callback, str):
                    info[callback_name] = getattr(cls, callback)
            
            # Make sure dependencies are registered first
            pg = info.get("type")
            if pg:
                if pg in parents:
                    chain = ", ".join(parent.__name__ for parent in parents)+", "+pg.__name__
                    raise TypeError(f"Recursive reference in PropertyGroup chain: {chain}")
                if pg.__name__.endswith(":AUTOREGISTER") or (pg in self.classes_new):
                    self.__register_class(pg, parents)
        
        return prop_infos
    
    def __register_class_attributes(self, cls, prop_infos):
        # We need to remove all properties before registration, because:
        # 1) post-registration modifications to props defined via annotations are ignored
        # 2) register_class() complains if props are defined via attribtes
        annotations = getattr(cls, "__annotations__", None)
        for key in prop_infos.keys():
            if annotations: annotations.pop(key, None)
            if key in cls.__dict__: delattr(cls, key)
            # Note: __dict__ is mappingproxy, which does not support item deletion
        
        try:
            bpy.utils.register_class(cls)
        except Exception as exc:
            print(f"addon {self.name}: could not register {cls}\n{exc}")
            raise
        
        # Add props back, this time as attributes only
        for key, info in prop_infos.items():
            setattr(cls, key, info())
    
    def __register_class_annotations(self, cls, prop_infos):
        # Put all bpy props into annotations (so that Blender won't complain)
        annotations = getattr(cls, "__annotations__", None)
        if annotations is None:
            annotations = {}
            cls.__annotations__ = annotations
        
        for key, info in prop_infos.items():
            if key in cls.__dict__: delattr(cls, key)
            if key not in annotations: annotations[key] = info()
        
        try:
            bpy.utils.register_class(cls)
        except Exception as exc:
            print(f"addon {self.name}: could not register {cls}\n{exc}")
            raise
    
    __support_attr_props = {bpy.types.PropertyGroup}
    def __register_class(self, cls, parents=()):
        if cls in self.classes: return
        
        prop_infos = self.__register_class_props(cls, parents)
        
        if self.__support_attr_props.isdisjoint(cls.__bases__):
            self.__register_class_annotations(cls, prop_infos)
        else:
            self.__register_class_attributes(cls, prop_infos)
        
        self.classes[cls] = None
        self.classes_new.pop(cls, None)
        if self.status in ('INITIALIZATION', 'REGISTRATION'):
            if cls not in self.classes_static:
                self.classes_static[cls] = None
    
    def register(self):
        if self.status == 'REGISTERED':
            while self.classes_new:
                self.__register_class(self.classes_new.popitem(last=False)[0])
            return
        
        self.status = 'REGISTRATION'
        
        for cls in self.classes_static:
            self.__register_class(cls)
        
        while self.classes_new:
            self.__register_class(self.classes_new.popitem(last=False)[0])
        
        # ATTENTION: trying to access preferences cached from previous session
        # will crash Blender. We must ensure the cached value is up-to-date.
        userprefs = bpy.context.preferences
        if self.module_name in userprefs.addons:
            self._preferences = userprefs.addons[self.module_name].preferences
        else:
            self._preferences = None
        
        # Infer whether external/internal storages are required
        # by looking at whether any properties were added
        # for them before register() was invoked
        
        if BpyProp.is_in(self.External):
            self.type_extend("WindowManager", self.storage_name_external, self.External)
        
        if BpyProp.is_in(self.Internal):
            self.type_extend("Screen", self.storage_name_internal, self.Internal)
        
        # Don't clear objects_static_info! We need it if addon is disabled->enabled again.
        for reg_info in self.objects_static_info:
            getattr(self, reg_info[0])(*reg_info[1:])
        
        for callback in self._on_register:
            callback()
        
        self.status = 'REGISTERED'
    
    def unregister_keymaps(self):
        kc = bpy.context.window_manager.keyconfigs.addon
        if not kc: return
        
        op_idnames = set()
        menu_idnames = set()
        panel_idnames = set()
        for cls in self.classes:
            if not hasattr(cls, "bl_idname"): continue # just in case
            if issubclass(cls, bpy.types.Operator): op_idnames.add(cls.bl_idname)
            elif issubclass(cls, bpy.types.Menu): menu_idnames.add(cls.bl_idname)
            elif issubclass(cls, bpy.types.Panel): panel_idnames.add(cls.bl_idname)
        
        for km in kc.keymaps:
            for kmi in tuple(km.keymap_items):
                if kmi.idname in op_idnames:
                    km.keymap_items.remove(kmi)
                elif (kmi.idname in ("wm.call_menu", "wm.call_menu_pie")) and (kmi.properties.name in menu_idnames):
                    km.keymap_items.remove(kmi)
                elif (kmi.idname == "wm.call_panel") and (kmi.properties.name in panel_idnames):
                    km.keymap_items.remove(kmi)
    
    def unregister(self):
        self.status = 'UNREGISTRATION'
        
        for callback in self._on_unregister:
            callback()
        
        self.remove(all=True)
        
        self._preferences = None
        
        self.unregister_keymaps()
        
        for cls in reversed(self.classes):
            try:
                bpy.utils.unregister_class(cls)
            except RuntimeError:
                print(f"addon {self.name}: could not unregister {cls}")
        
        self.classes.clear()
        self.classes_new.clear() # in case something was added but not registered
        
        self.status = 'UNREGISTERED'
    
    #========================================================================#
    
    # ===== REGISTRABLE TYPES DECORATORS ===== #
    def __add_class(self, cls, mixins=None):
        if mixins:
            if isinstance(mixins, type): mixins = (mixins,)
            add_mixins(cls, *mixins)
        
        self.classes_new[cls] = None # value isn't used
    
    def __gen_pg(self, cls, mixins=None):
        cls = ensure_baseclass(cls, bpy.types.PropertyGroup)
        self.__add_class(cls, mixins=mixins)
        return cls
    
    def PropertyGroup(self, cls=None, **kwargs):
        if cls: return self.__gen_pg(cls, **kwargs)
        return (lambda cls: self.__gen_pg(cls, **kwargs))
    
    def __gen_func_decorators(self, base, bl_attrs, funcs, func_name):
        # Note: be careful with closures, they capture actual scope's
        # variables instead of their values -- so all closures capturing
        # an iterated variable will be using its last value.
        
        def make_subdecorator(cls, func, _func_name):
            def subdecorator(_func):
                setattr(cls, _func_name, _func)
                return func
            setattr(func, _func_name, subdecorator)
        
        def func_decorator0(func, kwargs):
            cls = self.__func_to_bpy_class(base, func, func_name)
            cls = self.__add_idnamable(cls, base, kwargs, bl_attrs)
            func.bpy_class = cls
            for _func_name in funcs:
                make_subdecorator(cls, func, _func_name)
            return func
        
        def func_decorator(func=None, **kwargs):
            if func: return func_decorator0(func, kwargs)
            return (lambda func: func_decorator0(func, kwargs))
        
        return func_decorator
    
    def __gen_registrable_decorators(self):
        def gen_decorator(name, base, bl_attrs, funcs):
            def decorator(cls=None, **kwargs):
                if cls: return self.__add_idnamable(cls, base, kwargs, bl_attrs)
                return (lambda cls: self.__add_idnamable(cls, base, kwargs, bl_attrs))
            decorator.__name__ = decorator.__qualname__ = name
            setattr(self, name, decorator)
            
            for func_name in funcs:
                func_decorator = self.__gen_func_decorators(base, bl_attrs, funcs, func_name)
                func_decorator.__name__ = func_decorator.__qualname__ = f"{name}.{func_name}"
                setattr(decorator, func_name, func_decorator)
        
        # All registrable types (except PropertyGroup)
        # seem to match this pattern (have bl_idname property)
        for name in dir(bpy.types):
            bpy_type = getattr(bpy.types, name)
            rna = bpy_type.bl_rna
            if rna.base: continue # this is a derived type
            if "bl_idname" not in rna.properties: continue
            bl_attrs = [p.identifier[3:] for p in rna.properties
                if p.identifier.startswith("bl_")]
            funcs = [p.identifier for p in rna.functions]
            gen_decorator(name, bpy_type, bl_attrs, funcs)

    def __add_idnamable(self, cls, base, kwargs, bl_attrs):
        cls = ensure_baseclass(cls, base)
        
        for key, value in kwargs.items():
            if key in bl_attrs: key = "bl_"+key
            if not hasattr(cls, key): setattr(cls, key, value)
        
        self.__autocomplete(cls, base, bl_attrs)
        
        self.__add_class(cls)
        
        return cls

    def __autocomplete(self, cls, base, bl_attrs):
        is_operator = (base is bpy.types.Operator)
        has_description = "description" in bl_attrs
        
        if (not hasattr(cls, "bl_idname")) and is_operator:
            cls.bl_idname = ".".join(p.lower() for p in cls.__name__.rsplit("_OT_", 1))
        
        if not hasattr(cls, "bl_label"): cls.bl_label = bpy.path.clean_name(cls.__name__)
        
        if hasattr(cls, "bl_label"): cls.bl_label = compress_whitespace(cls.bl_label)
        
        if hasattr(cls, "bl_description"):
            cls.bl_description = compress_whitespace(cls.bl_description)
        elif has_description and hasattr(cls, "__doc__"): # __doc__ can be None
            cls.bl_description = compress_whitespace(cls.__doc__ or "")
    
    # func->class coversion is useful for cases when the class
    # is basically a wrapper of a function which is used in
    # other places as an actual function. Or for more concise code.
    def __func_to_bpy_class(self, base, func, func_name):
        is_generator = inspect.isgeneratorfunction(func)
        is_function = not is_generator
        
        props_allowed = base not in (bpy.types.Panel, bpy.types.Menu, bpy.types.Header)
        
        n_positional, use_varargs, bpy_props = self.__func_args_to_bpy_props(func, props_allowed)
        
        cls = type(func.__name__, (base,), {})
        cls.__doc__ = func.__doc__
        
        for name, bpy_prop in bpy_props:
            setattr(cls, name, bpy_prop)
        
        if (base is bpy.types.Operator) and (func_name in {"execute", "invoke", "modal"}):
            func = self.__wrap_operator(func, func_name, n_positional, bpy_props)
        
        setattr(cls, func_name, func)
        
        return cls
    
    @staticmethod
    def __wrap_operator(func, func_name, n_positional, bpy_props):
        prop_names = [name for name, bpy_prop in bpy_props]
        def map_args(self):
            return {name:getattr(self, name) for name in prop_names}
        
        def map_result(result):
            if isinstance(result, set): return result
            return ({'CANCELLED'} if result is False else {'FINISHED'})
        
        if func_name == "execute":
            if n_positional == 2:
                return (lambda self, context: map_result(func(self, context, **map_args(self))))
            elif n_positional == 1:
                return (lambda self, context: map_result(func(context, **map_args(self))))
            else:
                return (lambda self, context: map_result(func(**map_args(self))))
        elif func_name in ("invoke", "modal"):
            if n_positional == 3:
                return (lambda self, context, event: map_result(func(self, context, event, **map_args(self))))
            elif n_positional == 2:
                return (lambda self, context, event: map_result(func(context, event, **map_args(self))))
            elif n_positional == 1:
                return (lambda self, context, event: map_result(func(event, **map_args(self))))
            else:
                return (lambda self, context, event: map_result(func(**map_args(self))))
    
    @staticmethod
    def __func_args_to_bpy_props(func, props_allowed=True):
        # func(a, b, c, d=1, e=2, f=3, *args, g=4, h=5, i=6, **kwargs)
        # * only args with default values can be converted to bpy props
        # * when func is called from wrapper class, the missing
        #   non-optional arguments will be substituted with None
        
        argspec = inspect.getfullargspec(func)
        args = argspec.args
        varargs = argspec.varargs
        varkw = argspec.varkw
        defaults = argspec.defaults
        kwonlyargs = argspec.kwonlyargs
        kwonlydefaults = argspec.kwonlydefaults
        annotations = argspec.annotations
        
        bpy_props = []
        
        n_optional = (0 if defaults is None else len(defaults))
        n_positional = len(args) - n_optional
        n_kwonly = (0 if kwonlyargs is None else len(kwonlyargs))
        use_varargs = bool(varargs) and (n_optional == 0)
        
        if props_allowed:
            empty_dict = {}
            
            def process_arg(name, value):
                annotation = annotations.get(name, empty_dict)
                
                try:
                    if isinstance(annotation, prop):
                        bpy_prop = value | annotation
                    else:
                        bpy_prop = value | prop(**annotation)
                except:
                    return value
                
                bpy_props.append((name, bpy_prop))
                
                if bpy_prop[0] == bpy.props.PointerProperty:
                    return None
                elif bpy_prop[0] == bpy.props.CollectionProperty:
                    return [] # maybe use a collection emulator?
                elif BpyProp.validate(value):
                    return bpy_prop[1].get("default", BpyProp.known[bpy_prop[0]])
                else:
                    return value
            
            if n_optional != 0:
                defaults = list(defaults)
                for i in range(n_optional):
                    name, value = args[n_positional + i], defaults[i]
                    defaults[i] = process_arg(name, value)
                func.__defaults__ = tuple(defaults)
            
            if n_kwonly != 0:
                for name in kwonlyargs:
                    value = kwonlydefaults[name]
                    kwonlydefaults[name] = process_arg(name, value)
                func.__kwdefaults__ = kwonlydefaults
        
        return n_positional, use_varargs, bpy_props
    
    #========================================================================#
    
    # ===== PRESETS ===== #
    
    def Preset(self, path, sorting='NAME', popup='PANEL', title=None, options={'ADD'}):
        """
        path # addon-relative path to a preset; multi-file if contains "{id}"
        sorting # NAME, CREATE, MODIFY, NONE (multi-file supports only NAME and MODIFY)
        popup # PANEL, OPERATOR or None
        title # what to display in the selector (if None, uses class name)
        options # INLINE, ADD, RESET, EDIT (selector UI tweaks)
        
        Added by the decorator:
        __init__(id, *args, **kwargs) # args, kwargs are passed to update()
        id # unique identifier in presets
        presets # collection of all presets of this type
        op_prefix
        op_add
        op_edit
        op_apply # if decorated class provides apply()
        op_move # if sorting is NONE
        op_delete
        op_reset
        draw_selector(layout, context) # draw contents of selector
        draw_popup(layout, text="", icon='PRESET') # for selector popup
        type_name # if not provided
        type_names # if not provided
        set_layout_state() # if not provided
        OpEditMixin # if not provided
        OpEditMixin.setup_operator(op, preset, context) # if not provided
        OpEditMixin.is_new
        
        Decorated class is expected to have:
        serialize() # should return list/tuple or dict of non-id properties
        update(..., context=None) # ...: all optional; same order/names as returned by serialize()
        apply(context) # [optional] may return string (error message) to be displayed
        name # [optional] for display in selector
        tooltip # [optional] expected to contain "{hint}"
        type_name # [optional] singular
        type_names # [optional] plural
        set_layout_state() # [optional]
        OpEditMixin # if not provided, add/edit will show a dialog with id
        OpEditMixin.setup_operator(op, preset, context) # [optional]
        
        Note: layout.context_pointer_set() can be useful for UI-local behavior
        """
        
        def decorator(preset_cls):
            preset_manager = PresetManager(self, preset_cls, path, sorting)
            
            def __init__(self, id, *args, **kwargs):
                if preset_manager.multifile:
                    id = BpyPath.clean_filename(id)
                self._id = id
                if hasattr(self, "update"):
                    self.update(*args, **kwargs)
            
            preset_cls.__init__ = __init__
            preset_cls.id = property(lambda self: self._id)
            # Note: don't make presets a property, since we need
            # to access it from the class too, not just instances
            preset_cls.presets = preset_manager
            
            preset_manager.load()
            
            tooltip = getattr(preset_cls, "tooltip", "{hint}")
            
            type_name = getattr(preset_cls, "type_name", None)
            if type_name is None:
                type_name = preset_cls.__name__.replace("_", " ")
                type_name = " ".join(split_camelcase(type_name))
                preset_cls.type_name = type_name
            tn_suffix = " "+type_name
            
            type_names = getattr(preset_cls, "type_names", None)
            if type_names is None:
                type_names = (type_name+"s" if type_name else "")
                preset_cls.type_names = type_names
            tn_suffixes = " "+type_names
            
            set_layout_state = getattr(preset_cls, "set_layout_state", None)
            if set_layout_state is None:
                set_layout_state = (lambda self, layout: None)
                preset_cls.set_layout_state = set_layout_state
            
            selector_title = (type_names+":" if title is None else title) # don't assign to title (it's an outside-scope name)
            
            show_inline = ('INLINE' in options)
            show_add = ('ADD' in options)
            show_reset = ('RESET' in options)
            show_edit = ('EDIT' in options)
            has_apply = hasattr(preset_cls, "apply")
            
            op_prefix = f"{self.module_name.lower()}.{preset_cls.__name__.lower()}"
            preset_cls.op_prefix = op_prefix
            
            OpEditMixin = getattr(preset_cls, "OpEditMixin", None)
            if not isinstance(OpEditMixin, type):
                class OpEditMixin:
                    id = "" | prop()
                    def execute(self, context):
                        if not self.id: return {'CANCELLED'}
                        preset_manager.add(preset_cls(self.id, context=context))
                        BlUI.tag_redraw()
                        return {'FINISHED'}
                    def invoke(self, context, event):
                        if not self.id: self.id = type_name
                        wm = context.window_manager
                        return wm.invoke_props_dialog(self, width=200)
                    def draw(self, context):
                        self.layout.alert = not self.id
                        self.layout.prop(self, "id", text="", translate=False)
                preset_cls.OpEditMixin = OpEditMixin # just in case
            
            if hasattr(OpEditMixin, "invoke"):
                invoke_prev = OpEditMixin.invoke
                def invoke(self, context, event):
                    # To avoid duplicate text in tooltips, operators have empty labels;
                    # but since we show a dialog here, we need the label/title
                    self.is_new = self.bl_idname.endswith("_add") # also useful elsewhere
                    if self.is_new:
                        self.bl_label = "Add"+tn_suffix
                    else:
                        self.bl_label = "Edit"+tn_suffix
                    return invoke_prev(self, context, event)
                OpEditMixin.invoke = invoke
            
            if not hasattr(OpEditMixin, "setup_operator"):
                OpEditMixin.setup_operator = (lambda op, preset, context: None)
            
            OpEditMixin.preset_cls = preset_cls
            OpEditMixin.preset_manager = preset_manager
            
            preset_cls.op_add = f"{op_prefix}_add"
            OpPresetAdd = add_mixins("OpPresetAdd", OpEditMixin)
            op_label = "Add"+tn_suffix
            self.Operator(idname=preset_cls.op_add, label="", description=tooltip.format(hint=op_label), options={'INTERNAL'})(OpPresetAdd)
            
            preset_cls.op_edit = f"{op_prefix}_edit"
            OpPresetEdit = add_mixins("OpPresetEdit", OpEditMixin)
            op_label = "Edit"+tn_suffix
            self.Operator(idname=preset_cls.op_edit, label="", description=tooltip.format(hint=op_label), options={'INTERNAL'})(OpPresetEdit)
            
            if has_apply:
                preset_cls.op_apply = f"{op_prefix}_apply"
                op_label = "Apply"+tn_suffix
                @self.Operator.execute(idname=preset_cls.op_apply, label="", description=tooltip.format(hint=op_label), options={'INTERNAL'})
                def OpPresetApply(self, context, id=""):
                    preset = preset_manager.get(id)
                    err = preset.apply(context)
                    if err:
                        self.report({'ERROR'}, err)
                        return False
                    BlUI.tag_redraw()
            
            if preset_manager.sorting == 'NONE':
                preset_cls.op_move = f"{op_prefix}_move"
                op_label = "Move"+tn_suffix
                @self.Operator.execute(idname=preset_cls.op_move, label="", description=tooltip.format(hint=op_label), options={'INTERNAL'})
                def OpPresetMove(id="", direction=0):
                    preset_manager.move(id, direction, relative=True)
                    BlUI.tag_redraw()
            
            preset_cls.op_delete = f"{op_prefix}_delete"
            op_label = "Delete"+tn_suffix
            @self.Operator.execute(idname=preset_cls.op_delete, label="", description=tooltip.format(hint=op_label), options={'INTERNAL'})
            def OpPresetDelete(id=""):
                preset_manager.delete(id)
                BlUI.tag_redraw()
            
            preset_cls.op_reset = f"{op_prefix}_reset"
            op_label = "Reset"+tn_suffixes
            @self.Operator(idname=preset_cls.op_reset, label=op_label, description=tooltip.format(hint=op_label), options={'INTERNAL'})
            class OpPresetReset:
                def execute(self, context):
                    preset_manager.reset()
                    BlUI.tag_redraw()
                    return {'FINISHED'}
                def invoke(self, context, event):
                    wm = context.window_manager
                    return wm.invoke_props_dialog(self, width=200)
            
            def draw_selector(layout, context):
                layout.operator_context = 'INVOKE_DEFAULT' # to be able to invoke operators from popup
                
                # Copy "metadata" from the parent context
                for ctx_name in dir(context):
                    if ctx_name.isidentifier(): continue
                    layout.context_pointer_set(ctx_name, None)
                
                col = layout.column(align=True)
                #col.alignment = 'LEFT'
                
                if selector_title or show_reset or show_add:
                    if not (selector_title or show_reset):
                        row_title = None
                        row_add = col
                    elif show_inline:
                        row_title = col.row(align=True)
                        row_add = row_title
                    else:
                        row_title = (col.row() if selector_title or show_reset else None)
                        row_add = (col if show_add else None)
                    
                    if show_reset: row_title.operator(preset_cls.op_reset, text="", icon='RECOVER_LAST')
                    if selector_title: row_title.label(text=selector_title)
                    
                    if show_add:
                        op = row_add.operator(preset_cls.op_add, text="", icon='ADD') # PRESET_NEW ?
                        if hasattr(op, "id"): op.id = ""
                        OpEditMixin.setup_operator(op, None, context)
                
                for preset in preset_manager:
                    row = col.row(align=True)
                    
                    name = getattr(preset, "name", preset.id)
                    
                    if has_apply:
                        row2 = row.row(align=True)
                        preset.set_layout_state(row2)
                        
                        op = row2.operator(preset_cls.op_apply, text=name, icon='NONE', translate=False)
                        op.id = preset.id
                        
                        if show_edit:
                            op = row.operator(preset_cls.op_edit, text="", icon='PREFERENCES') # TOOL_SETTINGS ?
                            if hasattr(op, "id"): op.id = preset.id
                            OpEditMixin.setup_operator(op, preset, context)
                    else:
                        preset.set_layout_state(row)
                        
                        op = row.operator(preset_cls.op_edit, text=name, icon='NONE', translate=False)
                        if hasattr(op, "id"): op.id = preset.id
                        OpEditMixin.setup_operator(op, preset, context)
                    
                    if preset_manager.sorting == 'NONE':
                        op = row.operator(preset_cls.op_move, text="", icon='TRIA_UP')
                        op.id = preset.id
                        op.direction = -1
                        
                        op = row.operator(preset_cls.op_move, text="", icon='TRIA_DOWN')
                        op.id = preset.id
                        op.direction = 1
                    
                    op = row.operator(preset_cls.op_delete, text="", icon='X')
                    op.id = preset.id
            
            preset_cls.draw_selector = draw_selector
            
            @classmethod
            def popup_poll(cls, context):
                return bool(preset_manager or selector_title or show_reset or show_add)
            
            if popup == 'PANEL':
                # Any space/region combination will do, as long as the panel won't be visible
                preset_cls.panel_selector = f"EMPTY_PT_{op_prefix.replace('.', '_')}_selector"
                @self.Panel(idname=preset_cls.panel_selector, label=type_names, space_type='CONSOLE', region_type='WINDOW')
                class PanelPresetSelector: # Note: panels don't have description/tooltip
                    poll = popup_poll
                    def draw(self, context):
                        preset_cls.draw_selector(self.layout, context)
                
                def draw_popup(layout, text="", icon='PRESET'):
                    layout.popover(preset_cls.panel_selector, text=text, icon=icon)
                preset_cls.draw_popup = draw_popup
            elif popup == 'OPERATOR':
                preset_cls.op_selector = f"{op_prefix}_selector"
                op_label = "Show"+tn_suffixes
                @self.Operator(idname=preset_cls.op_selector, label="", description=tooltip.format(hint=op_label), options={'INTERNAL'})
                class OpPresetSelector:
                    poll = popup_poll
                    def invoke(self, context, event):
                        def popup_draw(self, context):
                            preset_cls.draw_selector(self.layout, context)
                        context.window_manager.popover(popup_draw)
                        return {'FINISHED'}
                
                def draw_popup(layout, text="", icon='PRESET'):
                    layout.operator(preset_cls.op_selector, text=text, icon=icon)
                preset_cls.draw_popup = draw_popup
            
            # Import / Export preset(s) #
            # ========================= #
            preset_cls.op_import = f"{op_prefix}_import"
            @self.Operator(idname=preset_cls.op_import, label="Import preset(s)", description="Import preset(s)", options={'INTERNAL'})
            class OpPresetImport:
                files: [bpy.types.OperatorFileListElement] | prop("File Path", "File path used for importing")
                directory: "" | prop()
                def invoke(self, context, event):
                    context.window_manager.fileselect_add(self)
                    return {'RUNNING_MODAL'}
                def execute(self, context):
                    paths = [os.path.join(self.directory, item.name) for item in self.files]
                    for path in paths:
                        if not os.path.isfile(path): continue
                        if preset_manager.multifile:
                            name = BpyPath.splitext(os.path.basename(path))[0]
                            for preset in preset_manager.read(path, name):
                                preset_manager.add(preset)
                        else:
                            for preset in preset_manager.read(path):
                                preset_manager.add(preset)
                    return {'FINISHED'}
            
            preset_cls.op_export = f"{op_prefix}_export"
            @self.Operator(idname=preset_cls.op_export, label="Export preset", description="Export preset", options={'INTERNAL'})
            class OpPresetExport:
                id: "" | prop(options={'HIDDEN'})
                filepath: "" | prop("File Path", "File path used for exporting", maxlen=1024, subtype='FILE_PATH')
                check_existing: True | prop("Check Existing", "Check and warn on overwriting existing files", options={'HIDDEN'})
                def invoke(self, context, event):
                    if not self.filepath:
                        blend_filepath = os.path.dirname(context.blend_data.filepath)
                        self.filepath = os.path.join(blend_filepath, self.id)
                    context.window_manager.fileselect_add(self)
                    return {'RUNNING_MODAL'}
                def execute(self, context):
                    if self.id:
                        preset = preset_manager.get(self.id)
                    else:
                        name = BpyPath.splitext(os.path.basename(self.filepath))[0]
                        preset = preset_cls(name, context=context)
                    preset_manager.write(self.filepath, preset)
                    self.filepath = "" # reset
                    return {'FINISHED'}
            
            @self.context_menu('APPEND')
            def context_menu_draw(self, context):
                btn_op = getattr(context, "button_operator", None)
                if btn_op is None: return
                layout = self.layout
                op_idname = BpyOp.convert(btn_op.rna_type.identifier, py=True)
                if op_idname.startswith(op_prefix):
                    layout.separator()
                    op = layout.operator(preset_cls.op_import)
                    op = layout.operator(preset_cls.op_export)
                    op.id = getattr(btn_op, "id", "")
            
            return preset_cls
        
        return decorator

#========================================================================#

class PresetManager:
    """
    path
    sorting
    multifile

    load()
    save(id=None) # id is ignored for single-file presets
    reset()

    __repr__
    __str__
    __len__
    __bool__
    __contains__
    __iter__
    __reversed__
    __getitem__
    __setitem__
    __delitem__

    ids # iterator property
    get(id) # returns None if not found
    index(preset|id)
    clear()
    add(preset) # will overwrite preset with the same id
    delete(preset|id)
    move(index|preset, pos, relative=False)
    """
    
    # Yaml is not a standard python package and not bundled with Blender.
    # We could use Json, but it's not very convenient to write by hand.
    # Actual Python code is convenient, but has no "pretty-print" option.
    # For convenience, use a mix of Python/Json (each preset is a separate
    # Python expression, except True/False/None are true/false/null).
    
    addon_presets_subdir = BpyPath.join("scripts", "presets", "addon")
    addon_presets_dir = BpyPath.join(bpy.utils.resource_path('USER'), addon_presets_subdir)
    
    path = property(lambda self: self.__path)
    sorting = property(lambda self: self.__sorting)
    multifile = property(lambda self: self.__multifile)
    
    def __init__(self, addon, preset_cls, path, sorting):
        self.__addon = addon
        self.__preset_cls = preset_cls
        
        self.__path = BpyPath.normslash(path) # relative to (addon_presets_dir + addon idname) and/or addon path
        self.__subdirs = os.path.dirname(self.__path)
        self.__filename = os.path.basename(self.__path)
        self.__ext = os.path.splitext(self.__filename)[1].lower()
        
        # addon.path is the directory containing the main module file
        self.__builtin_dir = BpyPath.join(os.path.realpath(self.__addon.path), self.__subdirs)
        self.__presets_dir = BpyPath.join(self.addon_presets_dir, self.__addon.module_name, self.__subdirs)
        self.__builtin_file = BpyPath.join(self.__builtin_dir, self.__filename)
        self.__presets_file = BpyPath.join(self.__presets_dir, self.__filename)
        self.__mark_file = self.__presets_file+"!"
        
        self.__presets_subdir = BpyPath.join(self.addon_presets_subdir, self.__addon.module_name, self.__subdirs)
        self.__presets_subfile = BpyPath.join(self.__presets_subdir, self.__filename)
        self.__mark_subfile = self.__presets_subfile+"!"
        
        self.__multifile = "{id}" in self.__filename
        
        if self.__multifile:
            if sorting == 'CREATE': sorting = 'MODIFY'
            if sorting != 'MODIFY': sorting = 'NAME'
        
        self.__sorting = sorting
        if self.__sorting == 'NAME':
            self.__add = self.__add_NAME
            self.__move = self.__move_dummy
            self.__replace_item = self.__replace_item_NAME
        elif self.__sorting == 'CREATE':
            self.__add = self.__add_first
            self.__move = self.__move_dummy
            self.__replace_item = self.__replace_item_other
        elif self.__sorting == 'MODIFY':
            self.__add = self.__add_first
            self.__move = self.__move_dummy
            self.__replace_item = self.__replace_item_MODIFY
        else:
            self.__sorting = 'NONE'
            self.__add = self.__add_last
            self.__move = self.__move_NONE
            self.__replace_item = self.__replace_item_other
        
        self.__ordered = []
        self.__hashed = {}
    
    class __JsonToPy(ast.NodeTransformer):
        _names_to_consts = {"true":True, "false":False, "null":None}
        def visit_Name(self, node):
            try:
                value = self._names_to_consts[node.id]
                return ast.copy_location(ast.NameConstant(value=value), node)
            except KeyError:
                return node
    
    def read(self, path, name=None):
        try:
            with open(path, "r") as f:
                txt = f.read()
        except IOError:
            print(f"Error while trying to read preset \"{path}\"")
            print(traceback.format_exc())
            return
        
        try:
            node = ast.parse(txt)
        except (SyntaxError, ValueError):
            print(f"Error while trying to parse preset \"{path}\"")
            print(traceback.format_exc())
            return
        
        node = self.__JsonToPy().visit(node)
        for subnode in node.body:
            if not isinstance(subnode, ast.Expr):
                print(f"Error while trying to deserialize preset \"{path}\"")
                print(f"({subnode.lineno}, {subnode.col_offset}): unexpected syntax (not an expression)")
                continue
            
            try:
                data = ast.literal_eval(subnode.value)
            except ValueError:
                print(f"Error while trying to deserialize preset \"{path}\"")
                print(f"({subnode.lineno}, {subnode.col_offset}): could not evaluate preset")
                print(traceback.format_exc())
                continue
            
            try:
                preset = None
                if name is None:
                    if isinstance(data, str):
                        preset = self.__preset_cls(data)
                    elif isinstance(data, (list, tuple)):
                        preset = self.__preset_cls(*data)
                    elif isinstance(data, dict):
                        preset = self.__preset_cls(**data)
                else:
                    if isinstance(data, str):
                        preset = self.__preset_cls(name, data)
                    elif isinstance(data, (list, tuple, set)):
                        preset = self.__preset_cls(name, *data)
                    elif isinstance(data, dict):
                        preset = self.__preset_cls(name, **data)
            except Exception:
                print(f"Error while trying to deserialize preset \"{path}\"")
                print(f"({subnode.lineno}, {subnode.col_offset}): could not initialize preset")
                print(traceback.format_exc())
                continue
            
            if preset is not None: yield preset
    
    def load(self): # sync runtime data with the file(s)
        self.__clear()
        if not self.__path: return False
        
        path = self.__find_path()
        if not os.path.exists(path): return False
        
        if self.__multifile:
            items = []
            for filepath, name, ext in self.__iter_preset_files(path):
                if name in self.__hashed: continue # not supposed to happen
                sortkey = (os.path.getmtime(filepath) if self.__sorting == 'MODIFY' else name)
                for preset in self.read(filepath, name):
                    items.append((sortkey, preset))
                    self.__hashed[preset.id] = preset
                    break
            
            items.sort(key=(lambda item: item[0]))
            self.__ordered.extend(item[1] for item in items)
        else:
            for preset in self.read(path):
                if preset.id in self.__hashed: continue
                self.__ordered.append(preset)
                self.__hashed[preset.id] = preset
        
        return True
    
    def __find_path(self):
        # Note: presets aren't shared among different Blender versions
        # bpy.utils.resource_path('USER') returns e.g. '.../blender/2.80'
        # Presets are in e.g. '.../blender/2.80/scripts/presets'
        # If user presets was not found in the current version dir,
        # search in previous version directories.
        
        # self.__mark_file indicates "use factory presets".
        
        if self.__multifile:
            if os.path.exists(self.__mark_file): return self.__builtin_dir
            if os.path.isdir(self.__presets_dir): return self.__presets_dir
        else:
            if os.path.exists(self.__mark_file): return self.__builtin_file
            if os.path.isfile(self.__presets_file): return self.__presets_file
        
        def try_parse(s):
            n = 0
            for c in s:
                if not c.isdecimal(): break
                n += 1
            if n == 0: return 0
            if n == len(s): return int(s)
            return int(s[:n])+0.5
        
        def parse_version(s):
            return tuple(try_parse(part) for part in s.split("."))
        
        usr_path_curr = bpy.utils.resource_path('USER')
        curr_version = parse_version(os.path.basename(usr_path_curr))
        usr_path_root = os.path.dirname(usr_path_curr)
        versions = []
        for fsname in os.listdir(usr_path_root):
            fspath = BpyPath.join(usr_path_root, fsname)
            if not os.path.isdir(fspath): continue
            versions.append((parse_version(fsname), fspath))
        versions.sort()
        versions, paths = [v for v, p in versions], [p for v, p in versions]
        
        i = binary_search(versions, curr_version, insert=-1)
        for path in reversed(paths[:i]):
            if self.__multifile:
                mark_file = BpyPath.join(path, self.__mark_subfile)
                if os.path.exists(mark_file) and os.path.exists(BpyPath.dirname(self.__mark_file)):
                    shutil.copy2(mark_file, self.__mark_file)
                    return self.__builtin_dir
                
                presets_dir = BpyPath.join(path, self.__presets_subdir)
                if os.path.isdir(presets_dir) and os.path.exists(BpyPath.dirname(self.__presets_dir)):
                    shutil.copytree(presets_dir, self.__presets_dir)
                    return presets_dir
            else:
                mark_file = BpyPath.join(path, self.__mark_subfile)
                if os.path.exists(mark_file) and os.path.exists(BpyPath.dirname(self.__mark_file)):
                    shutil.copy2(mark_file, self.__mark_file)
                    return self.__builtin_file
                
                presets_file = BpyPath.join(path, self.__presets_subfile)
                if os.path.isfile(presets_file) and os.path.exists(BpyPath.dirname(self.__presets_file)):
                    shutil.copy2(presets_file, self.__presets_file)
                    return presets_file
        
        if self.__multifile:
            return self.__builtin_dir
        else:
            return self.__builtin_file
    
    def __iter_preset_files(self, path):
        for filename in os.listdir(path):
            filepath = BpyPath.join(path, filename)
            if not os.path.isfile(filepath): continue
            name, ext = BpyPath.splitext(filename)
            if ext.lower() != self.__ext: continue
            yield filepath, name, ext
    
    def write(self, path, *presets):
        def default(value):
            if isinstance(value, set): return list(value)
            raise TypeError(f"{value} is not JSON serializable")
        
        indent = (4 if self.__multifile else None)
        
        datas = []
        for preset in presets:
            if preset is None: continue
            if hasattr(preset, "serialize"):
                data = preset.serialize()
                if not self.__multifile:
                    if isinstance(data, list):
                        data.insert(0, preset.id)
                    elif isinstance(data, tuple):
                        data = [preset.id] + list(data)
                    elif isinstance(data, dict):
                        data["id"] = preset.id
            else:
                data = preset.id
            data = json.dumps(data, sort_keys=True, indent=indent, default=default)
            datas.append(data)
        
        if self.__multifile and (presets and not datas):
            # presets consists of Nones; delete the file
            if os.path.isfile(path): os.remove(path)
            return
        
        path_dir = os.path.dirname(path)
        if not os.path.exists(path_dir): os.makedirs(path_dir)
        
        try:
            with open(path, "w") as f:
                f.write("\n".join(datas))
        except IOError as exc:
            print(exc)
            return
    
    def save(self, id=None): # sync the file(s) with runtime data
        if not self.__path: return False
        
        if not os.path.exists(self.__presets_dir): os.makedirs(self.__presets_dir)
        path = self.__presets_file
        
        reseted = os.path.exists(self.__mark_file)
        
        os_remove_all(self.__mark_file)
        
        if self.__multifile:
            if (id is None) or reseted:
                ids = set(self.__hashed.keys())
                for filepath, name, ext in self.__iter_preset_files(self.__presets_dir):
                    ids.add(name)
            else:
                ids = [id]
            
            for id in ids:
                preset = self.__hashed.get(id) # if absent, delete the file
                self.write(path.replace("{id}", id), preset)
        else:
            self.write(path, *self.__ordered)
        
        return True
    
    def reset(self):
        if not self.__path: return False
        
        path = (self.__presets_dir if self.__multifile else self.__presets_file)
        if not os.path.exists(path): return False
        
        if self.__multifile:
            rmtree(path, remove_top=False)
        else:
            os_remove_all(path)
        
        self.write(self.__mark_file)
        time.sleep(0.05)
        
        self.load()
        
        return True
    
    def __clear(self):
        self.__ordered.clear()
        self.__hashed.clear()
    
    def __add_NAME(self, preset):
        i = binary_search(self.__ordered, preset, key=(lambda item: item.id), insert=True)
        self.__ordered.insert(i, preset)
        self.__hashed[preset.id] = preset
    
    def __add_first(self, preset):
        self.__ordered.insert(0, preset)
        self.__hashed[preset.id] = preset
    
    def __add_last(self, preset):
        self.__ordered.append(preset)
        self.__hashed[preset.id] = preset
    
    def __delete(self, preset):
        if isinstance(preset, str): preset = self.get(preset)
        try:
            self.__ordered.remove(preset)
        except ValueError:
            return False
        self.__hashed.pop(preset.id, None)
        return True
    
    def __move_NONE(self, item, pos, relative):
        i0 = (item if isinstance(item, int) else self.index(item))
        if i0 is None: return False
        n = len(self.__ordered)
        if (i0 < 0) or (i0 >= n): return False
        if relative: pos = i0 + pos
        i1 = min(max(pos, 0), n-1)
        if i0 == i1: return False
        if abs(i1-i0) == 1:
            self.__ordered[i0], self.__ordered[i1] = self.__ordered[i1], self.__ordered[i0]
        else:
            self.__ordered.insert(i1, self.__ordered.pop(i0))
        return True
    
    def __move_dummy(self, item, pos, relative):
        return False
    
    def __repr__(self):
        cls_name = self.__preset_cls.__name__
        ids_str = ", ".join(repr(preset.id) for preset in self.__ordered)
        return f"<{cls_name}>: {ids_str}"
    
    __str__ = __repr__
    
    def __len__(self):
        return len(self.__ordered)
    
    def __bool__(self):
        return bool(self.__ordered)
    
    def __contains__(self, item):
        if isinstance(item, str): return item in self.__hashed
        return item in self.__ordered
    
    def __iter__(self):
        return self.__ordered.__iter__()
    
    def __reversed__(self):
        return self.__ordered.__reversed__()
    
    def __getitem__(self, id):
        if isinstance(id, int):
            return self.__ordered[id]
        elif isinstance(id, str):
            return self.__hashed[id]
        else:
            raise TypeError(f"id must be integer or string, not {type(id)}")
    
    def __setitem__(self, id, preset):
        if not isinstance(preset, self.__preset_cls):
            raise TypeError(f"value must be instance of {self.__preset_cls}")
        
        if isinstance(id, int):
            i, id = (id, self.__ordered[id].id)
        elif isinstance(id, str):
            i = self.index(id)
        else:
            raise TypeError(f"id must be integer or string, not {type(id)}")
        
        if i is not None: i = self.__replace_item(i, id, preset)
        
        if i is None:
            self.__add(preset)
        else:
            self.__ordered[i] = preset
            self.__hashed[preset.id] = preset
        self.save(preset.id)
    
    def __replace_item_NAME(self, i, id, preset):
        if preset.id != id:
            self.__delete(id)
            if self.__multifile: self.save(id)
            i = self.index(preset.id)
        return i
    
    def __replace_item_MODIFY(self, i, id, preset):
        if preset.id != id:
            self.__delete(id)
            if self.__multifile: self.save(id)
        self.__delete(preset.id)
        return None
    
    def __replace_item_other(self, i, id, preset):
        if preset.id != id:
            del self.__hashed[id]
            if self.__multifile: self.save(id)
            j = self.index(preset.id)
            if j is not None:
                del self.__ordered[j]
                if j < i: i -= 1
        return i
    
    def __delitem__(self, id):
        if isinstance(id, int):
            i, id = (id, self.__ordered[id].id)
        elif isinstance(id, str):
            i = self.index(id)
        else:
            raise TypeError(f"id must be integer or string, not {type(id)}")
        
        if i is None:
            raise KeyError(repr(id))
        else:
            if self.__delete(id): self.save(id)
    
    @property
    def ids(self):
        for preset in self.__ordered: yield preset.id
    
    def get(self, id):
        return self.__hashed.get(id)
    
    def index(self, preset):
        if isinstance(preset, str):
            for i, _preset in enumerate(self.__ordered):
                if _preset.id == preset: return i
            return None
        else:
            try:
                return self.__ordered.index(preset)
            except ValueError:
                return None
    
    def clear():
        self.__clear()
        self.save()
    
    def add(self, preset):
        self[preset.id] = preset
    
    def delete(self, preset):
        id = (preset if isinstance(preset, str) else preset.id)
        if self.__delete(preset): self.save(id)
    
    def move(self, item, pos, relative=False):
        if self.__move(item, pos, relative): self.save()
