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
    "name": "Mouselook Navigation",
    "author": "dairin0d, moth3r",
    "version": (1, 6, 0),
    "blender": (2, 80, 0),
    "location": "View3D > orbit/pan/dolly/zoom/fly/walk",
    "description": "Provides extra 3D view navigation options (ZBrush mode) and customizability",
    "warning": "Beta version",
    "wiki_url": "https://github.com/dairin0d/blender-2.8-scripts/blob/master/docs/mouselook_navigation/mouselook_navigation.md",
    "tracker_url": "https://github.com/dairin0d/blender-2.8-scripts/issues/new?labels=mouselook+navigation",
    "category": "3D View"
}

#============================================================================#

import bpy
import bgl
import gpu
import bmesh

from mathutils import Color, Vector, Matrix, Quaternion, Euler

import math
import time
import os
import json
import itertools

import importlib
import importlib.util

if "dairin0d" in locals(): importlib.reload(utils_navigation)

if "dairin0d" in locals(): importlib.reload(dairin0d)
exec(("" if importlib.util.find_spec("dairin0d") else "from . ")+"import dairin0d")

dairin0d.load(globals(), {
    "utils_blender": "ToggleObjectMode, BlUtil",
    "utils_view3d": "SmartView3D, RaycastResult",
    "utils_userinput": "InputKeyParser, InputKeyMonitor, ModeStack, KeyMapUtils",
    "utils_gl": "cgl",
    "utils_ui": "NestedLayout, BlUI",
    "bpy_inspect": "prop, BlRna, BlEnums",
    "utils_addon": "AddonManager",
})

from . import utils_navigation
from .utils_navigation import trackball, apply_collisions, calc_selection_center

addon = AddonManager()
settings = addon.settings

"""
Note: due to the use of timer, operator consumes more resources than Blender's default
ISSUES:
* correct & stable collision detection?
* Blender's trackball
* ortho-grid/quadview-clip/projection-name display is not updated (do the issues disappear in 2.73 / Gooseberry branch?)
* Blender doesn't provide information about current selection center (try to use snap-cursor for this?), last paint/sculpt stroke
* zoom/rotate around last paint/sculpt stroke? (impossible: sculpt/paint is modal, and Blender provides mouse coordinates only for invoke/modal operator callbacks)

In the wiki:
* explain the rules for key setup (how the text is interpreted)
* explain fly/walk modes (e.g. what the scrollwheel does)
* other peculiarities of the algorithms I use?
* put a warning that if "emulate 3 mouse button" is enabled, the Alt key pan won't work in ZBrush preset (in this case, Alt+LMB will emulate middle mouse button)
* put a warning that User Preferences make the addon slow

Config/Presets:
* Load/Save/Import/Export config (+move almost everything from preferences to config)
* Load/Save/Import/Export presets

Keymaps:
* Generic solution for keymap registration
* remove default keymap behavior and use a default preset instead?
    * the default keymap behavior is actually a special case, since it takes the shortcut from the default navigation operator
    * incorporate this feature into the generic key-registration mechanism?
* implement key combinations in InputKeyMonitor? (e.g. Ctrl+Shift+LeftMouse and the like)

* In Blender 2.73, Grease Pencil can be edited (and points can be selected)

* Option to create an undo record when navigating from a camera?
* Add optional shortcuts to move focus to certain locations? (current orbit point, active element, selection center, cursor, world origin)
* Navigation history?
* Create camera here?


For fun: more game-like controls?
* Walk, run, crouch, crawl?
* Jump, hover/jetpack/glide?
* Climb? Grab ledge / shimmy / pull up / drop down?
* Run+jump: running jump?
* Crouch+jump: higher jump?
* Teleport / grappling hook
* Push/pull objects? "Telekinesis / gravity gun"?
* Auto stop before cliffs?
* Auto hover when there's nothing underneath?
* Head bob?
* "Swimming" mode?
* "Airplane" mode?
* "Quadcopter" mode?


GENERIC KEYMAP REGISTRATION:
* Addon may have several operators with auto-registrable shortcuts. For each of them, a separate list of keymaps must be displayed (or: use tabbed interface?)
    * For a specific operator, there is a list of keymaps (generic mechanism), followed by invocation options UI (the draw function must be supplied by the implementation)
    * List of keymap setups: each of the items is associated with (or includes?) its own operator invocation options, but it's also possible to use universal operator invocation options
        * Keymaps (to which the shortcuts will be inserted): by default, all possible keymaps are available, but the implementation may specify some restricted set.
            * Should be displayed as a list of toggles, but an enum property won't have enough bits for all keymaps. Use collection and custom-drawn menu with operators?
        * Event value and event type (string): this form allows to specify several alternative shortcuts, though for convenience we may provide a special UI with full_event properties
        * Modifiers: any/shift/ctrl/alt/oskey/other key
        * Insert after, Insert before: the generic default is ("*", ""), which simply appends to the end. However, the implementation mught supply its own default.
        * (not shown directly in the UI): is_current, index: index is the only thing that needs to be specified as an /actual/ operator invocation parameter; when the operator is invoked, it can easily find the appropriate configuration by index.
        * Included operator invocation options (if implemented as an include and not as a reference by index)
    * Invocation options are likely to include shortcuts/modes/transitions that work within the corresponding modal operator
* As a general rule, addon should clean up its keymaps on disabling, but it shouldn't erase the keymaps manually added by the user (is this actually possible?)
* When addon is enabled, there are 3 cases:
    * Addon is enabled for the first time: should execute default keymap auto-registration behavior
    * Addon is enabled on Blender startup: the autro-registration setup is verified by user and saved with user preferences, so default behavior shouldn't happen
    * Addon is enabled after being disabled: all user changes from preferences are lost, BUT: if there is a config saved in a file, the addon might try to recover at least some user configuration
    * Or maybe just always use config file and force user to manually save it?
* Presets (in general case, addon may have independent lists of presets for each separate feature, and each feature might include 1 or more operators)
    * in general case, presets can affect key setup, the properties with which the operator(s) will be invoked, and some global settings
    * Save (to presets directory), Load (from presets directory), Delete (from presets directory), Import (from user-specified location), Export (to user-specified location)
    * in some cases, the presets might want to use the same shortcuts as some built-in operators (the default shortcuts should still be provided in case the built-in operator was not found in keymaps)
    * should presets contain the difference from some default control scheme, or there shouldn't be any default other than some specially-matked preset?
    * Maybe it's better to make presets work for the whole addon, not just key setup? (this would make them a complete config setup)
    * Is is possible to generalize preset/config loading, or it has to be an implementation-specific callback?
    * Built-in presets may also be hard-coded (e.g. useful in case of single-file addons)

"""

@addon.PropertyGroup
class MouselookNavigation_InputSettings:
    modes = ['ORBIT', 'PAN', 'DOLLY', 'ZOOM', 'FLY', 'FPS']
    transitions = ['NONE:ORBIT', 'NONE:PAN', 'NONE:DOLLY', 'NONE:ZOOM', 'NONE:FLY', 'NONE:FPS', 'ORBIT:PAN', 'ORBIT:DOLLY', 'ORBIT:ZOOM', 'ORBIT:FLY', 'ORBIT:FPS', 'PAN:DOLLY', 'PAN:ZOOM', 'DOLLY:ZOOM', 'FLY:FPS']
    
    default_mode: 'ORBIT' | prop("Default mode", "Default mode", items=[(mode, f"Mode: {mode}", "") for mode in modes])
    allowed_transitions: set(transitions) | prop("Transitions", "Allowed transitions between modes", items=transitions)
    independent_modes: False | prop("Independent modes", "When switching to a different mode, use the mode's last position/rotation/zoom")
    
    zbrush_mode: 'NONE' | prop("ZBrush mode", "Invoke the operator only when mouse is over empty space or near the region border", items=[
        ('NONE', "ZBrush: Off", "Don't use ZBrush behavior"),
        ('SIMPLE', "ZBrush: Simple", "Use ZBrush behavior only when no modifier keys are pressed"),
        ('ALWAYS', "ZBrush: Always", "Always use ZBrush behavior"),
    ])
    
    origin_mode: 'PREFS' | prop("Orbit origin", "What to use as the orbit origin", items=[
        ('PREFS', "Origin: Auto", "Determine orbit origin from Blender's input preferences"),
        ('VIEW', "Origin: View", "Use 3D View's orbit center"),
        ('MOUSE', "Origin: Mouse", "Orbit around the point under the mouse"),
        ('SELECTION', "Origin: Selection", "Orbit around the selection pivot"),
    ])
    
    ortho_unrotate: True | prop("Ortho unrotate", "In Ortho mode, rotation is abandoned if another mode is selected")
    
    def _keyprop(name, default_keys, tooltip=""):
        return default_keys | prop(name, tooltip or name)
    keys_confirm: _keyprop("Confirm", "Ret, Numpad Enter, Left Mouse: Press")
    keys_cancel: _keyprop("Cancel", "Esc, Right Mouse: Press")
    keys_rotmode_switch: _keyprop("Trackball", "Space: Press", tooltip="Trackball on/off")
    keys_orbit: _keyprop("Orbit", "") # main operator key (MMB) by default
    keys_orbit_snap: _keyprop("Snap", "Alt", tooltip="Orbit Snap")
    keys_pan: _keyprop("Pan", "Shift")
    keys_dolly: _keyprop("Dolly", "")
    keys_zoom: _keyprop("Zoom", "Ctrl")
    keys_fly: _keyprop("Fly", "{Invoke key}: Double click")
    keys_fps: _keyprop("Walk", "Tab: Press")
    keys_fps_forward: _keyprop("FPS forward", "W, Up Arrow")
    keys_fps_back: _keyprop("FPS back", "S, Down Arrow")
    keys_fps_left: _keyprop("FPS left", "A, Left Arrow")
    keys_fps_right: _keyprop("FPS right", "D, Right Arrow")
    keys_fps_up: _keyprop("FPS up", "E, R, Page Up")
    keys_fps_down: _keyprop("FPS down", "Q, F, Page Down")
    keys_fps_acceleration: _keyprop("FPS fast", "Shift")
    keys_fps_slowdown: _keyprop("FPS slow", "Ctrl")
    keys_fps_crouch: _keyprop("FPS crouch", "Ctrl")
    keys_fps_jump: _keyprop("FPS jump", "Space")
    keys_fps_teleport: _keyprop("FPS teleport", "{Invoke key}, V")
    keys_x_only: _keyprop("X only", "X: Press", tooltip="Toggle X-axis input")
    keys_y_only: _keyprop("Y only", "Y: Press", tooltip="Toggle Y-axis input")
    
    # Must be a list to preserve enum order
    overrides_names = [
        "default_mode",
        "allowed_transitions",
        "independent_modes",
        "zbrush_mode",
        "origin_mode",
        "ortho_unrotate",
        "keys_confirm",
        "keys_cancel",
        "keys_rotmode_switch",
        "keys_orbit",
        "keys_orbit_snap",
        "keys_pan",
        "keys_dolly",
        "keys_zoom",
        "keys_fly",
        "keys_fps",
        "keys_fps_forward",
        "keys_fps_back",
        "keys_fps_left",
        "keys_fps_right",
        "keys_fps_up",
        "keys_fps_down",
        "keys_fps_acceleration",
        "keys_fps_slowdown",
        "keys_fps_crouch",
        "keys_fps_jump",
        "keys_fps_teleport",
        "keys_x_only",
        "keys_y_only",
    ]
    overrides_names_set = set(overrides_names)
    
    overrides: set() | prop("Override the default value", "", items=[(name, "") for name in overrides_names])
    
    overrides_dummy: False | prop("Cannot override (you are currently editing the default setup)", "")
    
    def draw(self, layout, main):
        is_main = main is None
        
        def draw_override(prop_name):
            # UNLOCKED LOCKED UNPINNED PINNED UNLINKED LINKED
            icon = ('PINNED' if prop_name in self.overrides else 'UNPINNED')
            with layout.row(align=True)(enabled=(not is_main), emboss=('PULLDOWN_MENU' if is_main else 'NORMAL')):
                if is_main:
                    layout.prop(self, "overrides_dummy", text="", icon=icon, toggle=True)
                else:
                    layout.prop_enum(self, "overrides", prop_name, text="", icon=icon)
        
        def draw_prop(data, prop_name, is_key=False, **kwargs):
            if (not is_main) and (prop_name not in self.overrides): data = main
            layout.active = is_main or (prop_name in self.overrides)
            if is_key: layout.alert = not InputKeyParser.validate(getattr(data, prop_name), invoke_key='INVOKE_KEY')
            layout.prop(data, prop_name, **kwargs)
        
        def draw_prop_with_override(data, prop_name, is_key=False, **kwargs):
            with layout.row(align=True):
                with layout.row(align=True):
                    draw_prop(data, prop_name, is_key=is_key, **kwargs)
                draw_override(prop_name)
        
        with layout.split(factor=0.15):
            with layout.column():
                with layout.row():
                    layout.label(text="Transitions:")
                    draw_override("allowed_transitions")
                with layout.column():
                    draw_prop(self, "allowed_transitions")
            
            with layout.column():
                with layout.row():
                    draw_prop_with_override(self, "default_mode", text="")
                    draw_prop_with_override(self, "independent_modes", text="Independent modes", toggle=True)
                with layout.row():
                    draw_prop_with_override(self, "zbrush_mode", text="")
                    draw_prop_with_override(self, "origin_mode", text="")
                    draw_prop_with_override(self, "ortho_unrotate", toggle=True)
                
                layout.separator()
                
                with layout.row():
                    with layout.column():
                        layout.label(text="Navigation shortcuts:")
                        draw_prop_with_override(self, "keys_confirm", is_key=True)
                        draw_prop_with_override(self, "keys_cancel", is_key=True)
                        draw_prop_with_override(self, "keys_rotmode_switch", is_key=True)
                        draw_prop_with_override(self, "keys_orbit", is_key=True)
                        draw_prop_with_override(self, "keys_orbit_snap", is_key=True)
                        draw_prop_with_override(self, "keys_pan", is_key=True)
                        draw_prop_with_override(self, "keys_dolly", is_key=True)
                        draw_prop_with_override(self, "keys_zoom", is_key=True)
                        draw_prop_with_override(self, "keys_fly", is_key=True)
                        draw_prop_with_override(self, "keys_fps", is_key=True)
                        draw_prop_with_override(self, "keys_x_only", is_key=True)
                        draw_prop_with_override(self, "keys_y_only", is_key=True)
                    with layout.column():
                        layout.label(text="FPS mode shortcuts:")
                        draw_prop_with_override(self, "keys_fps_forward", text="Forward", is_key=True)
                        draw_prop_with_override(self, "keys_fps_back", text="Back", is_key=True)
                        draw_prop_with_override(self, "keys_fps_left", text="Left", is_key=True)
                        draw_prop_with_override(self, "keys_fps_right", text="Right", is_key=True)
                        draw_prop_with_override(self, "keys_fps_up", text="Up", is_key=True)
                        draw_prop_with_override(self, "keys_fps_down", text="Down", is_key=True)
                        draw_prop_with_override(self, "keys_fps_acceleration", text="Faster", is_key=True)
                        draw_prop_with_override(self, "keys_fps_slowdown", text="Slower", is_key=True)
                        draw_prop_with_override(self, "keys_fps_crouch", text="Crouch", is_key=True)
                        draw_prop_with_override(self, "keys_fps_jump", text="Jump", is_key=True)
                        draw_prop_with_override(self, "keys_fps_teleport", text="Teleport", is_key=True)

@addon.Operator(idname="mouselook_navigation.navigate", label="Mouselook navigation", description="Mouselook navigation", options={'GRAB_CURSOR', 'BLOCKING'})
class MouselookNavigation:
    input_settings_id: 0 | prop("Input Settings ID", "Input Settings ID", min=0)
    
    def copy_input_settings(self, input_settings, universal_input_settings):
        def get_value(name):
            use_universal = (input_settings is None) or (name not in input_settings.overrides)
            return getattr(universal_input_settings if use_universal else input_settings, name)
        
        self.default_mode = get_value("default_mode")
        self.allowed_transitions = get_value("allowed_transitions")
        self.ortho_unrotate = get_value("ortho_unrotate")
        self.independent_modes = get_value("independent_modes")
        self.zbrush_mode = get_value("zbrush_mode")
        self.origin_mode = get_value("origin_mode")
    
    def create_keycheckers(self, event, input_settings, universal_input_settings):
        def get_value(name):
            use_universal = (input_settings is None) or (name not in input_settings.overrides)
            return getattr(universal_input_settings if use_universal else input_settings, name)
        
        self.keys_invoke = self.km.keychecker(event.type)
        if event.value in {'RELEASE', 'CLICK'}:
            self.keys_invoke_confirm = self.km.keychecker(event.type+":PRESS")
        else:
            self.keys_invoke_confirm = self.km.keychecker(event.type+":RELEASE")
        self.keys_confirm = self.km.keychecker(get_value("keys_confirm"))
        self.keys_cancel = self.km.keychecker(get_value("keys_cancel"))
        self.keys_rotmode_switch = self.km.keychecker(get_value("keys_rotmode_switch"))
        self.keys_orbit = self.km.keychecker(get_value("keys_orbit"))
        self.keys_orbit_snap = self.km.keychecker(get_value("keys_orbit_snap"))
        self.keys_pan = self.km.keychecker(get_value("keys_pan"))
        self.keys_dolly = self.km.keychecker(get_value("keys_dolly"))
        self.keys_zoom = self.km.keychecker(get_value("keys_zoom"))
        self.keys_fly = self.km.keychecker(get_value("keys_fly"))
        self.keys_fps = self.km.keychecker(get_value("keys_fps"))
        self.keys_fps_forward = self.km.keychecker(get_value("keys_fps_forward"))
        self.keys_fps_back = self.km.keychecker(get_value("keys_fps_back"))
        self.keys_fps_left = self.km.keychecker(get_value("keys_fps_left"))
        self.keys_fps_right = self.km.keychecker(get_value("keys_fps_right"))
        self.keys_fps_up = self.km.keychecker(get_value("keys_fps_up"))
        self.keys_fps_down = self.km.keychecker(get_value("keys_fps_down"))
        self.keys_fps_acceleration = self.km.keychecker(get_value("keys_fps_acceleration"))
        self.keys_fps_slowdown = self.km.keychecker(get_value("keys_fps_slowdown"))
        self.keys_fps_crouch = self.km.keychecker(get_value("keys_fps_crouch"))
        self.keys_fps_jump = self.km.keychecker(get_value("keys_fps_jump"))
        self.keys_fps_teleport = self.km.keychecker(get_value("keys_fps_teleport"))
        self.keys_x_only = self.km.keychecker(get_value("keys_x_only"))
        self.keys_y_only = self.km.keychecker(get_value("keys_y_only"))
    
    @classmethod
    def poll(cls, context):
        if not addon.settings.is_enabled: return False
        return (context.space_data.type == 'VIEW_3D')
    
    def modal(self, context, event):
        try:
            return self.modal_main(context, event)
        except:
            # If anything fails, at least dispose the resources
            self.cleanup(context)
            raise
    
    def modal_main(self, context, event):
        region = context.region
        v3d = context.space_data
        rv3d = context.region_data
        
        region_pos, region_size = self.sv.region_rect().get("min", "size", convert=Vector)
        
        userprefs = context.preferences
        drag_threshold = userprefs.inputs.drag_threshold
        move_threshold = userprefs.inputs.move_threshold
        mouse_double_click_time = userprefs.inputs.mouse_double_click_time / 1000.0
        rotate_method = userprefs.inputs.view_rotate_method
        invert_mouse_zoom = userprefs.inputs.invert_mouse_zoom
        invert_wheel_zoom = userprefs.inputs.invert_zoom_wheel
        use_zoom_to_mouse = userprefs.inputs.use_zoom_to_mouse
        use_auto_perspective = userprefs.inputs.use_auto_perspective
        
        flips = settings.flips
        
        use_zoom_to_mouse |= self.force_origin_mouse
        use_auto_perspective &= self.rotation_snap_autoperspective
        
        use_zoom_to_mouse |= (self.use_origin_selection and self.zoom_to_selection)
        
        walk_prefs = userprefs.inputs.walk_navigation
        teleport_time = walk_prefs.teleport_time
        walk_speed_factor = walk_prefs.walk_speed_factor
        use_gravity = walk_prefs.use_gravity
        view_height = walk_prefs.view_height
        jump_height = walk_prefs.jump_height
        
        self.km.update(event)
        
        prev_mode = self.mode_stack.mode
        self.mode_stack.update()
        mode = self.mode_stack.mode
        
        mouse_prev = Vector((event.mouse_prev_x, event.mouse_prev_y))
        mouse = Vector((event.mouse_x, event.mouse_y))
        mouse_offset = mouse - self.mouse0
        mouse_delta = mouse - mouse_prev
        mouse_region = mouse - region_pos
        
        self.input_axis_stack.update()
        if self.input_axis_stack.mode == 'Y':
            mouse_delta[0] = 0.0
        elif self.input_axis_stack.mode == 'X':
            mouse_delta[1] = 0.0
        
        if self.independent_modes and (mode != prev_mode) and (mode not in {'FLY', 'FPS'}):
            mode_state = self.modes_state[mode]
            self.sv.is_perspective = mode_state[0]
            self.sv.distance = mode_state[1]
            self.pos = mode_state[2].copy()
            self.sv.focus = self.pos
            self.rot = mode_state[3].copy()
            self.euler = mode_state[4].copy()
            if rotate_method == 'TURNTABLE':
                self.sv.turntable_euler = self.euler # for turntable
            else:
                self.sv.rotation = self.rot # for trackball
        
        if (prev_mode in {'FLY', 'FPS'}) and (mode not in {'FLY', 'FPS'}):
            focus_proj = self.sv.focus_projected + region_pos
            context.window.cursor_warp(focus_proj.x, focus_proj.y)
        
        # Attempt to match Blender's default speeds
        ZOOM_SPEED_COEF = -0.77
        ZOOM_WHEEL_COEF = -0.25
        TRACKBALL_SPEED_COEF = 0.35
        TURNTABLE_SPEED_COEF = 0.62
        
        clock = time.perf_counter()
        dt = 0.01
        speed_move = 2.5 * self.sv.distance# * dt # use realtime dt
        speed_zoom = Vector((1, 1)) * ZOOM_SPEED_COEF * dt
        speed_zoom_wheel = ZOOM_WHEEL_COEF
        speed_rot = TRACKBALL_SPEED_COEF * dt
        speed_euler = Vector((-1, 1)) * TURNTABLE_SPEED_COEF * dt
        speed_autolevel = 1 * dt
        
        if invert_mouse_zoom:
            speed_zoom *= -1
        if invert_wheel_zoom:
            speed_zoom_wheel *= -1
        
        if flips.orbit_x:
            speed_euler.x *= -1
        if flips.orbit_y:
            speed_euler.y *= -1
        if flips.zoom_x:
            speed_zoom.x *= -1
        if flips.zoom_y:
            speed_zoom.y *= -1
        if flips.zoom_wheel:
            speed_zoom_wheel *= -1
        
        speed_move *= self.fps_speed_modifier
        speed_zoom *= self.zoom_speed_modifier
        speed_zoom_wheel *= self.zoom_speed_modifier
        speed_rot *= self.rotation_speed_modifier
        speed_euler *= self.rotation_speed_modifier
        speed_autolevel *= self.autolevel_speed_modifier
        
        confirm = self.keys_confirm('PRESS')
        cancel = self.keys_cancel('PRESS')
        
        wheel_up = int(event.type == 'WHEELUPMOUSE')
        wheel_down = int(event.type == 'WHEELDOWNMOUSE')
        wheel_delta = wheel_up - wheel_down
        
        is_orbit_snap = False
        trackball_mode = self.trackball_mode
        
        if self.explicit_orbit_origin is not None:
            m_ofs = self.sv.matrix
            m_ofs.translation = self.explicit_orbit_origin
            m_ofs_inv = m_ofs.inverted()
        
        if (mode == 'FLY') or (mode == 'FPS'):
            if self.sv.is_region_3d or not self.sv.quadview_lock:
                self.explicit_orbit_origin = None
                self.sv.is_perspective = True
                self.sv.lock_cursor = False
                self.sv.lock_object = None
                self.sv.use_viewpoint = True
                self.sv.bypass_camera_lock = True
                trackball_mode = 'CENTER'
                
                mode = 'ORBIT'
                
                move_vector = self.fps_move_vector()
                
                if self.mode_stack.mode == 'FPS':
                    if move_vector.z != 0: # turn off gravity if manual up/down is used
                        use_gravity = False
                        walk_prefs.use_gravity = use_gravity
                    elif self.keys_fps_jump('ON'):
                        use_gravity = True
                        walk_prefs.use_gravity = use_gravity
                    
                    rotate_method = 'TURNTABLE'
                    min_speed_autolevel = 30 * dt
                    speed_autolevel = max(speed_autolevel, min_speed_autolevel)
                    
                    self.update_fly_speed(wheel_delta, True)
                    
                    is_teleport = self.keys_fps_teleport('PRESS')
                    
                    if not is_teleport: self.teleport_allowed = True
                    
                    if self.teleport_allowed and is_teleport:
                        raycast_result = self.sv.ray_cast(self.sv.project(self.sv.focus))
                        if raycast_result.success:
                            normal = raycast_result.normal
                            ray_data = self.sv.ray(self.sv.project(self.sv.focus))
                            if normal.dot(ray_data[1] - ray_data[0]) > 0: normal = -normal
                            self.teleport_time_start = clock
                            self.teleport_pos = raycast_result.location + normal * view_height
                            self.teleport_pos_start = self.sv.viewpoint
                    
                    if move_vector.magnitude > 0: self.teleport_pos = None
                else:
                    use_gravity = False
                    
                    self.update_fly_speed(wheel_delta, (move_vector.magnitude > 0))
                    
                    if self.keys_invoke('ON'):
                        self.fly_speed = Vector()
                        mode = 'PAN'
                
                self.rotate_method = rotate_method # used for FPS horizontal
                
                if (event.type == 'MOUSEMOVE') or (event.type == 'INBETWEEN_MOUSEMOVE'):
                    if mode == 'ORBIT':
                        if rotate_method == 'TURNTABLE':
                            self.change_euler(mouse_delta.y * speed_euler.y, mouse_delta.x * speed_euler.x, 0)
                        else: # 'TRACKBALL'
                            if flips.orbit_x:
                                mouse_delta.x *= -1
                            if flips.orbit_y:
                                mouse_delta.y *= -1
                            self.change_rot_mouse(mouse_delta, mouse, speed_rot, trackball_mode)
                        self.sync_view_orientation(False)
                    elif mode == 'PAN':
                        self.change_pos_mouse(mouse_delta, False)
                
                mode = self.mode_stack.mode # for display in header
                
                self.pos = self.sv.focus
        else:
            self.sv.use_viewpoint = False
            self.sv.bypass_camera_lock = self._lock_camera
            use_gravity = False
            self.teleport_pos = None
            self.teleport_allowed = False
            
            confirm |= self.keys_invoke_confirm('PRESS')
            
            if self.sv.can_move:
                if self.keys_rotmode_switch('ON') != (rotate_method == 'TRACKBALL'):
                    rotate_method = ('TURNTABLE' if rotate_method == 'TRACKBALL' else 'TRACKBALL')
                    userprefs.inputs.view_rotate_method = rotate_method
                self.rotate_method = rotate_method # used for FPS horizontal
                
                is_orbit_snap = self.keys_orbit_snap('ON')
                delta_orbit_snap = int(is_orbit_snap) - int(self.prev_orbit_snap)
                self.prev_orbit_snap = is_orbit_snap
                if delta_orbit_snap < 0:
                    self.euler = self.sv.turntable_euler
                    self.rot = self.sv.rotation
                
                if not self.sv.is_perspective:
                    if mode == 'DOLLY':
                        mode = 'ZOOM'
                    
                    # The goal is to make it easy to pan view without accidentally rotating it
                    if self.ortho_unrotate:
                        if mode in ('PAN', 'DOLLY', 'ZOOM'):
                            # forbid transitions back to orbit
                            self.mode_stack.remove_transitions({'ORBIT:PAN', 'ORBIT:DOLLY', 'ORBIT:ZOOM'})
                            self.reset_rotation(rotate_method, use_auto_perspective)
                
                if (event.type == 'MOUSEMOVE') or (event.type == 'INBETWEEN_MOUSEMOVE'):
                    if mode == 'ORBIT':
                        # snapping trackball rotation is problematic (I don't know how to do it)
                        if (rotate_method == 'TURNTABLE') or is_orbit_snap:
                            self.change_euler(mouse_delta.y * speed_euler.y, mouse_delta.x * speed_euler.x, 0)
                        else: # 'TRACKBALL'
                            if flips.orbit_x:
                                mouse_delta.x *= -1
                            if flips.orbit_y:
                                mouse_delta.y *= -1
                            self.change_rot_mouse(mouse_delta, mouse, speed_rot, trackball_mode)
                        
                        if use_auto_perspective:
                            self.sv.is_perspective = not is_orbit_snap
                        
                        if is_orbit_snap:
                            self.snap_rotation(self.rotation_snap_subdivs)
                        elif self.sv.is_perspective:
                            self.sync_view_orientation(False)
                    elif mode == 'PAN':
                        self.change_pos_mouse(mouse_delta, False)
                    elif mode == 'DOLLY':
                        if flips.dolly_y:
                            mouse_delta.y *= -1
                        self.change_pos_mouse(Vector((0.0, mouse_delta.y)), True)
                    elif mode == 'ZOOM':
                        self.change_distance((mouse_delta.y*speed_zoom.y + mouse_delta.x*speed_zoom.x), use_zoom_to_mouse)
                
                if wheel_delta != 0:
                    self.change_distance(wheel_delta * speed_zoom_wheel, use_zoom_to_mouse)
            else:
                if (event.type == 'MOUSEMOVE') or (event.type == 'INBETWEEN_MOUSEMOVE'):
                    if mode == 'PAN':
                        self.sv.camera_offset_pixels -= mouse_delta
                    elif mode == 'ZOOM':
                        self.sv.camera_zoom += (mouse_delta.y*speed_zoom.y + mouse_delta.x*speed_zoom.x) * -10
        
        dt = clock - self.clock
        self.clock = clock
        
        if event.type.startswith('TIMER'):
            if self.sv.can_move:
                if speed_autolevel > 0:
                    if (not is_orbit_snap) or (mode != 'ORBIT'):
                        if rotate_method == 'TURNTABLE':
                            self.change_euler(0, 0, speed_autolevel, False)
                        elif self.autolevel_trackball:
                            speed_autolevel *= 1.0 - abs(self.sv.forward.z)
                            self.change_euler(0, 0, speed_autolevel, self.autolevel_trackball_up)
            
            #context.area.tag_redraw()
        
        # In case the user wants to toggle FPS navigation keys (instead of hold), we have to process
        # movement on every event, not just on timer (otherwise toggle events will never trigger)
        if self.sv.can_move:
            if self.teleport_pos is None:
                abs_speed = self.calc_abs_speed(walk_speed_factor, speed_zoom, use_zoom_to_mouse, speed_move, use_gravity, dt, jump_height, view_height)
            else:
                abs_speed = self.calc_abs_speed_teleport(clock, dt, teleport_time)
            
            if abs_speed.magnitude > 0:
                self.change_pos(abs_speed)
        
        if self.explicit_orbit_origin is not None:
            pre_rotate_focus = m_ofs_inv @ self.pos
            m_ofs = self.sv.matrix
            m_ofs.translation = self.explicit_orbit_origin
            self.pos = m_ofs @ pre_rotate_focus
            self.sv.focus = self.pos
        
        self.modes_state[mode] = (self.sv.is_perspective, self.sv.distance, self.pos.copy(), self.rot.copy(), self.euler.copy())
        
        self.update_cursor_icon(context)
        txt = "{} (zoom={:.3f})".format(mode, self.sv.distance)
        context.area.header_text_set(txt)
        
        if confirm:
            self.cleanup(context)
            return {'FINISHED'}
        elif cancel:
            self.revert_changes()
            self.cleanup(context)
            return {'CANCELLED'}
        
        if settings.pass_through:
            # Arguably more useful? Allows to more easily combine navigation with other operations,
            # e.g. using mouse & NDOF device simultaneously or sculpt-rotate-sculpt-rotate without releasing the MMB
            return {'PASS_THROUGH'}
        else:
            return {'RUNNING_MODAL'}
    
    def calc_abs_speed(self, walk_speed_factor, speed_zoom, use_zoom_to_mouse, speed_move, use_gravity, dt, jump_height, view_height):
        abs_speed = Vector()
        
        fps_speed = self.calc_fps_speed(walk_speed_factor)
        if fps_speed.magnitude > 0:
            if not self.sv.is_perspective:
                self.change_distance((fps_speed.y * speed_zoom.y) * (-4), use_zoom_to_mouse)
                fps_speed.y = 0
            speed_move *= dt
            abs_speed = self.abs_fps_speed(fps_speed.x, fps_speed.y, fps_speed.z, speed_move, use_gravity)
        
        if use_gravity:
            gravity = -9.91
            self.velocity.z *= 0.999 # dampen
            self.velocity.z += gravity * dt
            is_jump = self.keys_fps_jump('ON')
            if is_jump:
                if self.velocity.z < 0:
                    self.velocity.z *= 0.9
                if not self.prev_jump:
                    self.velocity.z += jump_height
                self.velocity.z += (abs(gravity) + jump_height) * dt
            self.prev_jump = is_jump
            
            is_crouching = self.keys_fps_crouch('ON')
            
            scene = bpy.context.scene
            view_layer = bpy.context.view_layer
            
            pos0 = self.sv.viewpoint
            pos = pos0.copy()
            
            v0 = abs_speed
            v = abs_speed
            #v, collided = apply_collisions(scene, view_layer, pos, v0, view_height, is_crouching, False, 1)
            pos += v
            
            v0 = self.velocity * dt
            v, collided = apply_collisions(scene, view_layer, pos, v0, view_height, is_crouching, True, 0)
            if collided:
                self.velocity = Vector()
            pos += v
            
            abs_speed = pos - pos0
        else:
            self.velocity = Vector()
        
        return abs_speed
    
    def calc_abs_speed_teleport(self, clock, dt, teleport_time):
        p0 = self.sv.viewpoint
        t = (clock - self.teleport_time_start) + dt # +dt to move immediately
        if t >= teleport_time:
            p1 = self.teleport_pos
            self.teleport_pos = None
        else:
            t = t / teleport_time
            p1 = self.teleport_pos * t + self.teleport_pos_start * (1.0 - t)
        abs_speed = p1 - p0
        return abs_speed
    
    def update_fly_speed(self, wheel_delta, dont_fly=False):
        if dont_fly:
            self.fly_speed = Vector() # stop (FPS overrides flight)
            self.change_distance(wheel_delta*0.5)
        else:
            fwd_speed = self.fly_speed.y
            if (wheel_delta * fwd_speed < 0) and (abs(fwd_speed) >= 2):
                wheel_delta *= 2 # quick direction reversal
            fwd_speed = min(max(fwd_speed + wheel_delta, -9), 9)
            fwd_speed = round(fwd_speed) # avoid accumulation errors
            self.fly_speed.y = fwd_speed
    
    def fps_move_vector(self):
        move_forward = self.keys_fps_forward('ON')
        move_back = self.keys_fps_back('ON')
        move_left = self.keys_fps_left('ON')
        move_right = self.keys_fps_right('ON')
        move_up = self.keys_fps_up('ON')
        move_down = self.keys_fps_down('ON')
        
        move_x = int(move_right) - int(move_left)
        move_y = int(move_forward) - int(move_back)
        move_z = int(move_up) - int(move_down)
        
        return Vector((move_x, move_y, move_z))
    
    def calc_fps_speed(self, walk_speed_factor=5):
        move_vector = self.fps_move_vector()
        
        movement_accelerate = self.keys_fps_acceleration('ON')
        movement_slowdown = self.keys_fps_slowdown('ON')
        move_speedup = int(movement_accelerate) - int(movement_slowdown)
        if self.mode_stack.mode in {'PAN', 'DOLLY', 'ZOOM'}:
            move_speedup = 0
        
        fps_speed = move_vector * (walk_speed_factor ** move_speedup)
        
        if fps_speed.magnitude == 0:
            fps_speed = self.fly_speed.copy()
            fps_speed.x = self.calc_fly_speed(fps_speed.x)
            fps_speed.y = self.calc_fly_speed(fps_speed.y)
            fps_speed.z = self.calc_fly_speed(fps_speed.z)
        
        return fps_speed
    
    def calc_fly_speed(self, v, k=2):
        if round(v) == 0:
            return 0
        return math.copysign(2 ** (abs(v) - k), v)
    
    def change_distance(self, delta, to_explicit_origin=False):
        log_zoom = math.log(max(self.sv.distance, self.min_distance), 2)
        self.sv.distance = math.pow(2, log_zoom + delta)
        if to_explicit_origin and (self.explicit_orbit_origin is not None):
            dst = self.explicit_orbit_origin
            offset = self.pos - dst
            log_zoom = math.log(max(offset.magnitude, self.min_distance), 2)
            offset = offset.normalized() * math.pow(2, log_zoom + delta)
            self.pos = dst + offset
            self.sv.focus = self.pos
    
    def abs_fps_speed(self, dx, dy, dz, speed=1.0, use_gravity=False):
        xdir, ydir, zdir = self.sv.right, self.sv.forward, self.sv.up
        fps_horizontal = (self.fps_horizontal or use_gravity) and self.sv.is_perspective
        if (self.rotate_method == 'TURNTABLE') and fps_horizontal:
            ysign = (-1.0 if zdir.z < 0 else 1.0)
            zdir = Vector((0, 0, 1))
            ydir = Quaternion(zdir, self.euler.z) @ Vector((0, 1, 0))
            xdir = ydir.cross(zdir)
            ydir *= ysign
        return (xdir*dx + ydir*dy + zdir*dz) * speed
    
    def change_pos(self, abs_speed):
        self.pos += abs_speed
        self.sv.focus = self.pos
    
    def change_pos_mouse(self, mouse_delta, is_dolly=False):
        self.pos += self.mouse_delta_movement(mouse_delta, is_dolly)
        self.sv.focus = self.pos
    
    def mouse_delta_movement(self, mouse_delta, is_dolly=False):
        region = self.sv.region
        region_center = Vector((region.width*0.5, region.height*0.5))
        p0 = self.sv.unproject(region_center)
        p1 = self.sv.unproject(region_center - mouse_delta)
        pd = p1 - p0
        if is_dolly:
            pd_x = pd.dot(self.sv.right)
            pd_y = pd.dot(self.sv.up)
            pd = (self.sv.right * pd_x) + (self.sv.forward * pd_y)
        return pd
    
    def reset_rotation(self, rotate_method, use_auto_perspective):
        self.rot = self.rot0.copy()
        self.euler = self.euler0.copy()
        if rotate_method == 'TURNTABLE':
            self.sv.turntable_euler = self.euler # for turntable
        else:
            self.sv.rotation = self.rot # for trackball
        
        if use_auto_perspective:
            self.sv.is_perspective = self._perspective0
    
    numpad_orientations = [
        ('LEFT', Quaternion((0, 0, -1), math.pi/2.0)),
        ('RIGHT', Quaternion((0, 0, 1), math.pi/2.0)),
        ('BOTTOM', Quaternion((1, 0, 0), math.pi/2.0)),
        ('TOP', Quaternion((-1, 0, 0), math.pi/2.0)),
        ('FRONT', Quaternion((1, 0, 0, 0))),
        ('BACK', Quaternion((0, 0, 0, 1))),
        ('BACK', Quaternion((0, 0, 0, -1))),
    ]
    def detect_numpad_orientation(self, q):
        for name, nq in self.numpad_orientations:
            if abs(q.rotation_difference(nq).angle) < 1e-6: return name
    
    def sync_view_orientation(self, snap):
        numpad_orientation = self.detect_numpad_orientation(self.sv.rotation)
        if numpad_orientation and snap:
            bpy.ops.view3d.view_axis(type=numpad_orientation, align_active=False)
        else:
            bpy.ops.view3d.view_orbit(angle=0.0, type='ORBITUP')
    
    def snap_rotation(self, n=1):
        grid = math.pi*0.5 / n
        euler = self.euler.copy()
        euler.x = round(euler.x / grid) * grid
        euler.y = round(euler.y / grid) * grid
        euler.z = round(euler.z / grid) * grid
        self.sv.turntable_euler = euler
        self.rot = self.sv.rotation
        self.sync_view_orientation(True)
    
    def change_euler(self, ex, ey, ez, always_up=False):
        self.euler.x += ex
        self.euler.z += ey
        if always_up and (self.sv.up.z < 0) or (abs(self.euler.y) > math.pi*0.5):
            _pi = math.copysign(math.pi, self.euler.y)
            self.euler.y = _pi - (_pi - self.euler.y) * math.pow(2, -abs(ez))
        else:
            self.euler.y *= math.pow(2, -abs(ez))
        self.sv.turntable_euler = self.euler
        self.rot = self.sv.rotation # update other representation
    
    def change_rot_mouse(self, mouse_delta, mouse, speed_rot, trackball_mode):
        if trackball_mode == 'CENTER':
            mouse_delta *= speed_rot
            spin = -((self.sv.right * mouse_delta.x) + (self.sv.up * mouse_delta.y)).normalized()
            axis = spin.cross(self.sv.forward)
            self.rot = Quaternion(axis, mouse_delta.magnitude) @ self.rot
        elif trackball_mode == 'WRAPPED':
            mouse_delta *= speed_rot
            cdir = Vector((0, -1, 0))
            tv, x_neg, y_neg = self.trackball_vector(mouse)
            r = cdir.rotation_difference(tv)
            spin = r @ Vector((mouse_delta.x, 0, mouse_delta.y))
            axis = spin.cross(tv)
            axis = self.sv.matrix.to_3x3() @ axis
            self.rot = Quaternion(axis, mouse_delta.magnitude) @ self.rot
        else:
            # Glitchy/buggy. Consult with Dalai Felinto?
            region = self.sv.region
            mouse -= Vector((region.x, region.y))
            halfsize = Vector((region.width, region.height))*0.5
            p1 = (mouse - mouse_delta) - halfsize
            p2 = (mouse) - halfsize
            p1 = Vector((p1.x/halfsize.x, p1.y/halfsize.y))
            p2 = Vector((p2.x/halfsize.x, p2.y/halfsize.y))
            q = trackball(p1.x, p1.y, p2.x, p2.y, 1.1)
            axis, angle = q.to_axis_angle()
            axis = self.sv.matrix.to_3x3() @ axis
            q = Quaternion(axis, angle * speed_rot*200)
            self.rot = q @ self.rot
        self.rot.normalize()
        self.sv.rotation = self.rot # update other representation
        self.euler = self.sv.turntable_euler # update other representation
    
    def _wrap_xy(self, xy, m=1):
        region = self.sv.region
        x = xy.x % (region.width*m)
        y = xy.y % (region.height*m)
        return Vector((x, y))
    def trackball_vector(self, xy):
        region = self.sv.region
        region_halfsize = Vector((region.width*0.5, region.height*0.5))
        radius = region_halfsize.magnitude * 1.1
        xy -= Vector((region.x, region.y)) # convert to region coords
        xy = self._wrap_xy(xy, 2)
        x_neg = (xy.x >= region.width)
        y_neg = (xy.y >= region.height)
        xy = self._wrap_xy(xy)
        xy -= region_halfsize # make relative to center
        xy *= (1.0/radius) # normalize
        z = math.sqrt(1.0 - xy.length_squared)
        return Vector((xy.x, -z, xy.y)).normalized(), x_neg, y_neg
    
    def update_cursor_icon(self, context):
        # DEFAULT, NONE, WAIT, CROSSHAIR, MOVE_X, MOVE_Y, KNIFE, TEXT, PAINT_BRUSH, HAND, SCROLL_X, SCROLL_Y, SCROLL_XY, EYEDROPPER
        if self.mode_stack.mode in {'FLY', 'FPS'}:
            context.window.cursor_modal_set('NONE')
        else:
            context.window.cursor_modal_set('SCROLL_XY')
    
    def invoke(self, context, event):
        wm = context.window_manager
        userprefs = context.preferences
        region = context.region
        v3d = context.space_data
        rv3d = context.region_data
        
        if event.value == 'RELEASE':
            # 'ANY' is useful for click+doubleclick, but release is not intended
            # IMPORTANT: self.bl_idname is NOT the same as class.bl_idname!
            for kc, km, kmi in KeyMapUtils.search(MouselookNavigation.bl_idname):
                if (kmi.type == event.type) and (kmi.value == 'ANY'):
                    return {'CANCELLED'}
        
        if settings.is_using_universal_input_settings:
            universal_input_settings = settings.universal_input_settings
            input_settings = None
        else:
            universal_input_settings = settings.universal_input_settings
            input_settings_id = min(self.input_settings_id, len(settings.autoreg_keymaps)-1)
            input_settings = settings.autoreg_keymaps[input_settings_id].input_settings
        
        self.copy_input_settings(input_settings, universal_input_settings)
        
        self.sv = SmartView3D(context, use_matrix=True)
        
        region_pos, region_size = self.sv.region_rect().get("min", "size", convert=Vector)
        clickable_region_pos, clickable_region_size = self.sv.region_rect(False).get("min", "size", convert=Vector)
        
        self.zbrush_border = settings.calc_zbrush_border_size(self.sv.area, self.sv.region)
        
        self.km = InputKeyMonitor(event)
        self.create_keycheckers(event, input_settings, universal_input_settings)
        mouse_prev = Vector((event.mouse_prev_x, event.mouse_prev_y))
        mouse = Vector((event.mouse_x, event.mouse_y))
        mouse_delta = mouse - mouse_prev
        mouse_region = mouse - region_pos
        mouse_clickable_region = mouse - clickable_region_pos
        
        self.input_axis_stack = ModeStack({'X':self.keys_x_only, 'Y':self.keys_y_only}, {'XY:X', 'XY:Y', 'X:Y'}, 'XY', search_direction=1)
        
        self.zoom_to_selection = settings.zoom_to_selection
        
        if self.origin_mode == 'SELECTION':
            self.force_origin_mouse = False
            self.force_origin_selection = True
            self.use_origin_mouse = False
            self.use_origin_selection = True
        elif self.origin_mode == 'MOUSE':
            self.force_origin_mouse = True
            self.force_origin_selection = False
            self.use_origin_mouse = True
            self.use_origin_selection = False
        elif self.origin_mode == 'VIEW':
            self.force_origin_mouse = False
            self.force_origin_selection = False
            self.use_origin_mouse = False
            self.use_origin_selection = False
        else:
            self.force_origin_mouse = False
            self.force_origin_selection = False
            self.use_origin_mouse = userprefs.inputs.use_mouse_depth_navigate
            self.use_origin_selection = userprefs.inputs.use_rotate_around_active
        
        is_sculpt = (context.mode == 'SCULPT')
        is_dyntopo = False
        
        ignore_raycast = context.mode not in settings.raycast_modes
        use_raycast = (not ignore_raycast) and (self.use_origin_mouse or (self.zbrush_mode != 'NONE'))
        
        # If a mesh has face data, Blender will automatically disable dyntopo on re-entering sculpt mode
        if is_sculpt and use_raycast:
            is_dyntopo = context.object.use_dynamic_topology_sculpting
            if is_dyntopo: bpy.ops.sculpt.dynamic_topology_toggle()
        
        depthcast_radius = settings.zbrush_radius
        raycast_radius = min(settings.zbrush_radius, 16)
        
        cast_result = RaycastResult()
        
        if use_raycast:
            # Note: Auto Depth is useless with ZBrush mode anyway
            if settings.zbrush_method == 'ZBUFFER':
                cast_result = self.depth_cast(context, mouse_region, depthcast_radius)
            elif settings.zbrush_method == 'RAYCAST':
                with ToggleObjectMode('OBJECT' if is_sculpt else None):
                    cast_result = self.sv.ray_cast(mouse_region, raycast_radius)
        
        self.explicit_orbit_origin = None
        if self.use_origin_selection:
            self.explicit_orbit_origin = calc_selection_center(context, True)
        elif self.use_origin_mouse:
            if cast_result.success:
                self.explicit_orbit_origin = cast_result.location
                if self.sv.is_perspective:
                    # Blender adjusts distance so that focus and z-point lie in the same plane
                    viewpoint = self.sv.viewpoint
                    self.sv.distance = self.sv.z_distance(self.explicit_orbit_origin)
                    self.sv.viewpoint = viewpoint
            else:
                self.explicit_orbit_origin = self.sv.unproject(mouse_region)
        
        mode_keys = {'ORBIT':self.keys_orbit, 'PAN':self.keys_pan, 'DOLLY':self.keys_dolly, 'ZOOM':self.keys_zoom, 'FLY':self.keys_fly, 'FPS':self.keys_fps}
        self.mode_stack = ModeStack(mode_keys, self.allowed_transitions, self.default_mode, 'NONE')
        self.mode_stack.update()
        
        if (self.mode_stack.mode == 'NONE') or (self.zbrush_mode == 'ALWAYS'):
            if self.zbrush_mode != 'NONE':
                mouse_region_11 = clickable_region_size - mouse_clickable_region
                wrk_x = min(mouse_clickable_region.x, mouse_region_11.x)
                wrk_y = min(mouse_clickable_region.y, mouse_region_11.y)
                wrk_pos = min(wrk_x, wrk_y)
                
                if wrk_pos > self.zbrush_border:
                    if use_raycast and (settings.zbrush_method == 'SELECTION'):
                        with ToggleObjectMode('OBJECT' if is_sculpt else None):
                            cast_result = self.sv.select(mouse_region)
                    
                    if cast_result.success or ignore_raycast:
                        if is_dyntopo: bpy.ops.sculpt.dynamic_topology_toggle()
                        return {'PASS_THROUGH'}
            
            if self.mode_stack.mode == 'NONE':
                self.mode_stack.mode = self.default_mode
        
        if is_dyntopo: bpy.ops.sculpt.dynamic_topology_toggle()
        
        self.update_cursor_icon(context)
        
        self.color_crosshair_visible = settings.get_color("color_crosshair_visible")
        self.color_crosshair_obscured = settings.get_color("color_crosshair_obscured")
        self.color_zbrush_border = settings.get_color("color_zbrush_border")
        self.show_crosshair = settings.show_crosshair
        self.show_focus = settings.show_focus
        self.show_zbrush_border = settings.show_zbrush_border
        
        self.fps_horizontal = settings.fps_horizontal
        self.trackball_mode = settings.trackball_mode
        self.fps_speed_modifier = settings.fps_speed_modifier
        self.zoom_speed_modifier = settings.zoom_speed_modifier
        self.rotation_snap_subdivs = settings.rotation_snap_subdivs
        self.rotation_snap_autoperspective = settings.rotation_snap_autoperspective
        self.rotation_speed_modifier = settings.rotation_speed_modifier
        self.autolevel_trackball = settings.autolevel_trackball
        self.autolevel_trackball_up = settings.autolevel_trackball_up
        self.autolevel_speed_modifier = settings.autolevel_speed_modifier
        
        self.prev_orbit_snap = False
        self.min_distance = 2 ** -10
        
        self.fly_speed = Vector()
        
        # Starting from Blender 2.80, enabling the "Lock Camera to View" option
        # makes Blender take control of the camera matrix, so manipulating the
        # camera from scipt doesn't have any effect.
        self._lock_camera = self.sv.lock_camera
        self.sv.lock_camera = False
        self.sv.bypass_camera_lock = self._lock_camera
        
        self._clock0 = time.perf_counter()
        self._continuous0 = userprefs.inputs.use_mouse_continuous
        self._mouse0 = Vector((event.mouse_x, event.mouse_y))
        self._perspective0 = self.sv.is_perspective
        self._distance0 = self.sv.distance
        self._pos0 = self.sv.focus
        self._rot0 = self.sv.rotation
        self._euler0 = self.sv.turntable_euler
        self._smooth_view0 = userprefs.view.smooth_view
        
        self.mouse0 = self._mouse0.copy()
        self.clock0 = self._clock0
        self.pos = self._pos0.copy()
        self.rot0 = self._rot0.copy()
        self.rot = self.rot0.copy()
        self.euler0 = self._euler0.copy()
        self.euler = self.euler0.copy()
        
        self.modes_state = {}
        for mode in MouselookNavigation_InputSettings.modes:
            self.modes_state[mode] = (self.sv.is_perspective, self.sv.distance, self.pos.copy(), self.rot.copy(), self.euler.copy())
        
        self.clock = self.clock0
        self.velocity = Vector()
        self.prev_jump = False
        self.teleport_pos = None
        self.teleport_pos_start = None
        self.teleport_time_start = -1
        self.teleport_allowed = False
        
        self.sculpt_levels0 = None
        if self.should_adjust_multires(context):
            for modifier in context.object.modifiers:
                if modifier.type == 'MULTIRES':
                    self.sculpt_levels0 = modifier.sculpt_levels
                    modifier.sculpt_levels = min(modifier.sculpt_levels, 1)
                    break
        
        userprefs.inputs.use_mouse_continuous = True
        userprefs.view.smooth_view = 0
        
        self.register_handlers(context)
        
        context.area.header_text_set(None)
        
        # We need the view to redraw so that crosshair would appear
        # immediately after user presses MMB
        context.area.tag_redraw()
        
        return {'RUNNING_MODAL'}
    
    def depth_cast(self, context, mouse_region, depthcast_radius):
        result = {}
        
        def draw_callback():
            cast_result = self.sv.depth_cast(mouse_region, depthcast_radius)
            result["cast_result"] = cast_result
        
        context = bpy.context
        view3d = context.space_data
        prefs_system = context.preferences.system
        
        # Important: if viewport_aa is not OFF, 1-frame artifacts may appear after bpy.ops.wm.redraw_timer()
        
        viewport_aa = prefs_system.viewport_aa
        show_relationship_lines = view3d.overlay.show_relationship_lines
        show_motion_paths = view3d.overlay.show_motion_paths
        show_reconstruction = view3d.show_reconstruction
        show_gizmo = view3d.show_gizmo
        shading_type = view3d.shading.type
        show_xray = view3d.shading.show_xray
        show_shadows = view3d.shading.show_shadows
        show_cavity = view3d.shading.show_cavity
        use_dof = view3d.shading.use_dof
        
        prefs_system.viewport_aa = 'OFF'
        view3d.overlay.show_relationship_lines = False
        view3d.overlay.show_motion_paths = False
        view3d.show_reconstruction = False
        view3d.show_gizmo = False
        view3d.shading.type = 'SOLID'
        view3d.shading.show_xray = False
        view3d.shading.show_shadows = False
        view3d.shading.show_cavity = False
        view3d.shading.use_dof = False
        
        handler = addon.draw_handler_add(bpy.types.SpaceView3D, draw_callback, (), 'WINDOW', 'POST_PIXEL')
        bpy.ops.wm.redraw_timer(type='DRAW', iterations=1)
        addon.remove(handler)
        
        prefs_system.viewport_aa = viewport_aa
        view3d.overlay.show_relationship_lines = show_relationship_lines
        view3d.overlay.show_motion_paths = show_motion_paths
        view3d.show_reconstruction = show_reconstruction
        view3d.show_gizmo = show_gizmo
        view3d.shading.type = shading_type
        view3d.shading.show_xray = show_xray
        view3d.shading.show_shadows = show_shadows
        view3d.shading.show_cavity = show_cavity
        view3d.shading.use_dof = use_dof
        
        return result["cast_result"]
    
    def revert_changes(self):
        self.sv.bypass_camera_lock = True
        self.sv.use_viewpoint = False
        self.sv.rotation = self._rot0
        self.sv.distance = self._distance0
        self.sv.focus = self._pos0
        self.sv.is_perspective = self._perspective0
        self.mode_stack.mode = None # used for setting mouse position
    
    def cleanup(self, context):
        # Whether the navigation was confirmed or canceled,
        # we need to revert lock_camera to its initial state
        self.sv.lock_camera = self._lock_camera
        
        if self.mode_stack.mode is None:
            context.window.cursor_warp(self.mouse0.x, self.mouse0.y)
        elif self.mode_stack.mode in {'FLY', 'FPS'}:
            focus_proj = self.sv.focus_projected + self.sv.region_rect().get("min", convert=Vector)
            context.window.cursor_warp(focus_proj.x, focus_proj.y)
        
        if self.should_adjust_multires(context):
            for modifier in context.object.modifiers:
                if modifier.type == 'MULTIRES':
                    modifier.sculpt_levels = self.sculpt_levels0
                    break
        
        userprefs = context.preferences
        userprefs.inputs.use_mouse_continuous = self._continuous0
        userprefs.view.smooth_view = self._smooth_view0
        
        self.unregister_handlers(context)
        
        context.area.header_text_set(None)
        context.window.cursor_modal_restore()
        
        # We need the view to redraw so that crosshair would disappear
        # immediately after user releases MMB
        context.area.tag_redraw()
    
    def register_handlers(self, context):
        wm = context.window_manager
        wm.modal_handler_add(self)
        self._timer = addon.event_timer_add(1.0/settings.animation_fps, context.window)
        self._handle_view = addon.draw_handler_add(bpy.types.SpaceView3D, draw_callback_view, (self, context), 'WINDOW', 'POST_VIEW')
    
    def unregister_handlers(self, context):
        addon.remove(self._timer)
        addon.remove(self._handle_view)
    
    def should_adjust_multires(self, context):
        if not settings.adjust_multires: return False
        return (context.mode == 'SCULPT') and context.tool_settings.sculpt.show_low_resolution

def draw_crosshair(self, context, use_focus):
    if not self.sv.can_move:
        return # if camera can't be manipulated, crosshair is meaningless
    
    focus_proj = None
    if use_focus:
        if self.explicit_orbit_origin and not self.show_focus:
            return
        alpha = (0.4 if self.explicit_orbit_origin else 1.0)
        focus_proj = self.sv.focus_projected
        z_ref = self.sv.z_distance(self.sv.focus, 0.01)
    elif self.explicit_orbit_origin:
        alpha = 1.0
        focus_proj = self.sv.project(self.explicit_orbit_origin)
        z_ref = self.explicit_orbit_origin
    
    if focus_proj is None:
        return
    
    l0, l1 = 16, 25
    lines = [(Vector((0, l0)), Vector((0, l1))), (Vector((0, -l0)), Vector((0, -l1))),
             (Vector((l0, 0)), Vector((l1, 0))), (Vector((-l0, 0)), Vector((-l1, 0)))]
    lines = [(self.sv.unproject(p0 + focus_proj, z_ref, True),
              self.sv.unproject(p1 + focus_proj, z_ref, True)) for p0, p1 in lines]
    
    verts = list(itertools.chain(*lines))
    
    color = self.color_crosshair_visible
    color_visible = (color[0], color[1], color[2], 1.0*alpha)
    color = self.color_crosshair_obscured
    color_obscured = (color[0], color[1], color[2], 0.35*alpha)
    
    shader = gpu.shader.from_builtin('3D_UNIFORM_COLOR')
    shader.bind()
    with cgl('DepthFunc', 'LineWidth', BLEND=True, DEPTH_TEST=True, DepthMask=False):
        for c, df, lw in ((color_visible, 'LEQUAL', 1), (color_obscured, 'GREATER', 3)):
            cgl.DepthFunc = df
            cgl.LineWidth = lw
            shader.uniform_float("color", c)
            batch = cgl.batch(shader, 'LINES', pos=verts)
            batch.draw(shader)

def draw_callback_view(self, context):
    if not settings.is_enabled: return
    
    if self.sv.region_data != context.region_data: return
    
    if self.show_crosshair:
        draw_crosshair(self, context, False)
        draw_crosshair(self, context, True)

def draw_callback_px(self, context):
    if not settings.is_enabled: return
    
    context = bpy.context # we need most up-to-date context
    
    if settings.show_zbrush_border and settings.zbrush_mode:
        area = context.area
        region = context.region
        
        full_rect = BlUI.calc_region_rect(area, region)
        clickable_rect = BlUI.calc_region_rect(area, region, False)
        border = settings.calc_zbrush_border_size(area, region)
        color = settings.get_color("color_zbrush_border")
        
        x, y = clickable_rect.min - full_rect.min
        w, h = clickable_rect.size
        
        shader = gpu.shader.from_builtin('2D_UNIFORM_COLOR')
        shader.bind()
        with cgl(BLEND=True):
            verts = [(x + border, y + border), (x + w-border, y + border),
                (x + w-border, y + h-border), (x + border, y + h-border)]
            shader.uniform_float("color", (color[0], color[1], color[2], 0.5))
            batch = cgl.batch(shader, 'LINE_LOOP', pos=verts)
            batch.draw(shader)

@addon.timer(persistent=True)
def background_timer_update():
    if not addon.runtime.keymaps_initialized:
        if not KeyMapUtils.exists(MouselookNavigation.bl_idname):
            # Important: we cannot do this immediately on registration,
            # or Blender will crash if the user tries to reload scripts
            # (specifically, the crash occurs when trying to serialize
            # operator properties of some WindowManager keymaps).
            update_keymaps(True)
        addon.runtime.keymaps_initialized = True
    
    if settings.auto_trackball:
        # In a timer, bpy.context.mode seems to always be 'OBJECT',
        # and context has a very reduced set of properties
        view_layer = bpy.context.view_layer
        mode = BlEnums.mode_from_object(view_layer.objects.active)
        settings.is_trackball = (mode in settings.auto_trackball_modes)
    
    return 0 # run each frame

# Blender's "Assign Shortcut" utility doesn't work with addon preferences and Internal-like
# properties, so in order to allow users to assign shortcuts, we have to use operators.
# Importantly, for this to be possible, we have to put them into a known category:
# https://blender.stackexchange.com/questions/70697/how-to-allow-setting-hotkey-in-add-on

@addon.Operator.execute(idname="mouselook_navigation.toggle_enabled", label="Enable/disable Mouselook Navigation")
def VIEW3D_OT_mouselook_navigation_toggle_enabled(self, context):
    settings.is_enabled = not settings.is_enabled
    BlUI.tag_redraw()

@addon.Operator.execute(idname="mouselook_navigation.toggle_trackball", label="Use Trackball orbiting method")
def VIEW3D_OT_mouselook_navigation_toggle_trackball(self, context):
    settings.is_trackball = not settings.is_trackball
    BlUI.tag_redraw()

@addon.Operator.execute(idname="mouselook_navigation.autoreg_keymaps_update", label="Update Autoreg Keymaps", description="Update auto-registered keymaps")
def update_keymaps(activate=True):
    idname = MouselookNavigation.bl_idname
    
    KeyMapUtils.remove(idname)
    
    if activate:
        # Attention: userprefs.addons[__name__] may not exist during unregistration
        context = bpy.context
        wm = context.window_manager
        
        #keymaps = wm.keyconfigs.addon.keymaps
        
        # For a specific operator, the same (exact clones) keymap items may need to be
        # inserted into keymaps of several modes (depending on what conflicts may arise).
        # Since we need mouselook operator to have higher priority than default navigation,
        # but lower priority than 3D manipulator, we have to put it into the user keymaps
        # (because only user keymaps actually store user modifications).
        # User may still want to have several mouselook shortcuts in one mode, e.g. if (s)he
        # wants standard Blender control scheme (mouse to orbit/pan/zoom, Shift+F to fly/walk)
        keymaps = wm.keyconfigs.user.keymaps
        
        # Note: I use ANY event by default, because otherwise the Fly-mode's default
        # "{Invoke key}: Double click" shortcut won't work (or we'd need multiple keymaps)
        
        if len(settings.autoreg_keymaps) == 0 and settings.use_default_keymap:
            default_preset = AutoregKeymapPreset.presets.get("Blender")
            if default_preset:
                error = default_preset.apply(context)
                if error: self.report({'ERROR'}, error)
        
        key_monitor = InputKeyMonitor()
        
        kmi_to_insert = {}
        
        for ark_id, ark in enumerate(settings.autoreg_keymaps):
            insert_before = set(v.strip() for v in ark.insert_before.split(","))
            insert_before.discard("")
            insert_after = set(v.strip() for v in ark.insert_after.split(","))
            insert_after.discard("")
            
            for mode_name in ark.keymaps:
                kmi_datas = kmi_to_insert.setdefault(mode_name, [])
                
                value_type = ark.value_type
                if ":" not in value_type: value_type += ": Press"
                
                for key in key_monitor.parse_keys(value_type):
                    if key.startswith("!"): continue
                    key_type, key_value = key.split(":")
                    kmi_data = dict(idname=idname, type=key_type, value=key_value,
                        any=ark.any, shift=ark.shift, ctrl=ark.ctrl, alt=ark.alt,
                        oskey=ark.oskey, key_modifier=ark.key_modifier,
                        properties=dict(input_settings_id=ark_id))
                    kmi_datas.append((insert_after, kmi_data, insert_before))
        
        for keymap_name, kmi_datas in kmi_to_insert.items():
            try:
                km = keymaps[keymap_name] # expected to exist in user keymaps
            except:
                continue
            KeyMapUtils.insert(km, kmi_datas)

@addon.PropertyGroup
class AutoRegKeymapInfo:
    mode_names = ['3D View', 'Object Mode', 'Mesh', 'Curve', 'Armature', 'Metaball', 'Lattice', 'Font', 'Pose', 'Vertex Paint', 'Weight Paint', 'Image Paint', 'Sculpt', 'Particle']
    keymaps: {'3D View'} | prop("Keymaps", "To which keymaps this entry should be added", items=mode_names)
    value_type: "" | prop("Type of event", "Type of event")
    any: False | prop("Any", "Any modifier")
    shift: False | prop("Shift", "Shift")
    ctrl: False | prop("Ctrl", "Ctrl")
    alt: False | prop("Alt", "Alt")
    oskey: False | prop("Cmd", "Cmd (OS key)")
    key_modifier: 'NONE' | prop("Key Modifier", "Regular key pressed as a modifier", items=[
        (item.identifier, item.name, item.description, item.icon, item.value)
        for item in bpy.types.KeyMapItem.bl_rna.properties["key_modifier"].enum_items])
    insert_after: "view3d.manipulator" | prop("Insert after", "Insert after the specified operators")
    insert_before: "*" | prop("Insert before", "Insert before the specified operators")
    input_settings: MouselookNavigation_InputSettings | prop()
    
    def get_is_current(self):
        userprefs = bpy.context.preferences
        return (settings.autoreg_keymap_id == self.index) and (not settings.is_using_universal_input_settings)
    def set_is_current(self, value):
        userprefs = bpy.context.preferences
        if value:
            settings.autoreg_keymap_id = self.index
            settings.use_universal_input_settings = False
        else:
            settings.use_universal_input_settings = True
    is_current: False | prop("(Un)select this keymap entry", "(if selected, its settings will be shown below)",
        get=get_is_current, set=set_is_current)
    index: 0 | prop()

@addon.Operator.execute(idname="mouselook_navigation.autoreg_keymap_add", label="Add Autoreg Keymap")
def add_autoreg_keymap(self, context):
    """Add auto-registered keymap"""
    wm = context.window_manager
    userprefs = context.preferences
    settings.use_default_keymap = False
    ark = settings.autoreg_keymaps.add()
    ark.index = len(settings.autoreg_keymaps)-1
    settings.autoreg_keymap_id = ark.index

@addon.Operator.execute(idname="mouselook_navigation.autoreg_keymap_remove", label="Remove Autoreg Keymap")
def remove_autoreg_keymap(self, context, index=0):
    """Remove auto-registered keymap"""
    wm = context.window_manager
    userprefs = context.preferences
    settings.use_default_keymap = False
    settings.autoreg_keymaps.remove(self.index)
    if settings.autoreg_keymap_id >= len(settings.autoreg_keymaps):
        settings.autoreg_keymap_id = len(settings.autoreg_keymaps)-1
    for i, ark in enumerate(settings.autoreg_keymaps):
        ark.index = i

@addon.Preset("presets/{id}.preset", sorting='NAME', popup='PANEL', title="Keymap presets", options={'INLINE', 'ADD', 'RESET'})
class AutoregKeymapPreset:
    _flips = ("orbit_x", "orbit_y", "dolly", "zoom_x", "zoom_y", "zoom_wheel")
    
    def serialize(self):
        return {"data": self.data}
    
    def update(self, data=None, context=None):
        if data is None:
            wm = context.window_manager
            userprefs = context.preferences
            
            data = dict(
                flips=[flip for flip in self._flips if getattr(settings.flips, flip)],
                universal=settings.use_universal_input_settings,
                settings=self._cleanup_overrides(BlRna.serialize(settings.universal_input_settings)),
                keymaps=[self._cleanup_ark_data(BlRna.serialize(ark)) for ark in settings.autoreg_keymaps],
            )
        else:
            self._fix_old_versions(data)
        
        self.data = data
    
    def _cleanup_overrides(self, input_settings_data):
        input_settings_data.pop("overrides", None)
        return input_settings_data
    
    def _cleanup_ark_data(self, ark_data):
        ark_data.pop("is_current", None)
        ark_data.pop("index", None)
        
        input_settings_data = ark_data.get("input_settings")
        if input_settings_data:
            # Note: only remove non-overridden settings if overrides actually exist
            overrides = input_settings_data.get("overrides")
            if overrides is not None:
                for key in tuple(input_settings_data.keys()):
                    if key in overrides: continue
                    del input_settings_data[key]
            self._cleanup_overrides(input_settings_data)
        
        return ark_data
    
    def _fix_old_versions(self, data):
        if not isinstance(data, dict): return
        
        universal_settings = data.get("settings")
        if universal_settings:
            self._cleanup_overrides(universal_settings)
        
        keymaps = data.get("keymaps")
        if keymaps and isinstance(keymaps, (list, tuple)):
            for keymap in keymaps:
                if not isinstance(keymap, dict): continue
                self._cleanup_ark_data(keymap)
    
    def _fill_defaults(self, ark_data, universal_settings_data):
        if not universal_settings_data: return ark_data
        
        ark_data = dict(ark_data)
        
        input_settings_data = ark_data.get("input_settings")
        input_settings_data = dict(input_settings_data) if input_settings_data else {}
        
        names_set = MouselookNavigation_InputSettings.overrides_names_set
        
        overrides = set()
        input_settings_data["overrides"] = overrides
        
        for key, value in universal_settings_data.items():
            if key in input_settings_data:
                if key in names_set: overrides.add(key)
            else:
                input_settings_data[key] = value
        
        ark_data["input_settings"] = input_settings_data
        
        return ark_data
    
    def apply(self, context):
        wm = context.window_manager
        userprefs = context.preferences
        
        settings.use_default_keymap = False
        
        flips = self.data.get("flips", ())
        settings.flips.orbit_x = "orbit_x" in flips
        settings.flips.orbit_y = "orbit_y" in flips
        settings.flips.dolly = "dolly" in flips
        settings.flips.zoom_x = "zoom_x" in flips
        settings.flips.zoom_y = "zoom_y" in flips
        settings.flips.zoom_wheel = "zoom_wheel" in flips
        
        settings.use_universal_input_settings = self.data.get("universal", True)
        universal_settings_data = self.data.get("settings")
        BlRna.reset(settings.universal_input_settings)
        BlRna.deserialize(settings.universal_input_settings, universal_settings_data)
        
        while settings.autoreg_keymaps:
            settings.autoreg_keymaps.remove(0)
        
        for ark_data in self.data.get("keymaps", tuple()):
            ark_data = self._fill_defaults(ark_data, universal_settings_data)
            ark = settings.autoreg_keymaps.add()
            BlRna.deserialize(ark, ark_data)
            ark.index = len(settings.autoreg_keymaps)-1
        settings.autoreg_keymap_id = len(settings.autoreg_keymaps)-1
        
        update_keymaps()

@addon.Panel(idname="VIEW3D_PT_mouselook_navigation", space_type='VIEW_3D', region_type='UI', label="Mouselook Navigation", category="View")
class VIEW3D_PT_mouselook_navigation:
    @classmethod
    def poll(cls, context):
        return addon.settings.show_in_shelf
    
    def draw_header(self, context):
        self.layout.operator("mouselook_navigation.toggle_enabled", text="",
            icon=('CHECKBOX_HLT' if settings.is_enabled else 'CHECKBOX_DEHLT'), emboss=False)
    
    def draw(self, context):
        layout = NestedLayout(self.layout)
        
        with layout.row():
            layout.label(text="Show/hide:")
            layout.prop(*settings("show_trackball"), text="", icon='ORIENTATION_GIMBAL')
            with layout.row(align=True):
                layout.prop(*settings("show_crosshair"), text="", icon='ADD')
                with layout.row(align=True)(active=settings.show_crosshair):
                    layout.prop(*settings("show_focus"), text="", icon='LIGHT_HEMI')
                layout.prop(*settings("show_zbrush_border"), text="", icon='SELECT_SET')
        
        with layout.column(align=True):
            layout.prop(*settings("zoom_speed_modifier"))
            layout.prop(*settings("rotation_speed_modifier"))
            layout.prop(*settings("fps_speed_modifier"))
        
        layout.prop(*settings("fps_horizontal"))
        layout.prop(*settings("zoom_to_selection"))
        
        with layout.box():
            with layout.row():
                layout.label(text="Orbit snap")
                layout.prop(*settings("rotation_snap_autoperspective"), text="To Ortho", toggle=True)
            layout.prop(*settings("rotation_snap_subdivs"), text="Subdivs")
        
        with layout.box():
            with layout.row():
                layout.label(text="Trackball")
                layout.prop(*settings("trackball_mode"), text="")
            with layout.row(align=True):
                layout.prop(*settings("autolevel_trackball"), text="Autolevel", toggle=True)
                with layout.row(align=True)(active=settings.autolevel_trackball):
                    layout.prop(*settings("autolevel_trackball_up"), text="Up", toggle=True)
        
        layout.prop(*settings("autolevel_speed_modifier"))

@addon.Panel(idname="VIEW3D_PT_mouselook_navigation_header_popover", label="Mouselook Navigation", space_type='CONSOLE', region_type='WINDOW')
class VIEW3D_PT_mouselook_navigation_header_popover:
    def draw(self, context):
        layout = self.layout
        layout.label(text="Mouselook Navigation")
        VIEW3D_PT_mouselook_navigation.draw(self, context)

@addon.ui_draw("VIEW3D_HT_header", 'APPEND')
def draw_view3d_header(self, context):
    layout = self.layout
    
    if settings.show_in_header:
        row = layout.row(align=True)
        
        if settings.show_trackball:
            row.operator("mouselook_navigation.toggle_trackball", text="", icon='ORIENTATION_GIMBAL', depress=settings.is_trackball)
        
        row.operator("mouselook_navigation.toggle_enabled", text="", icon='VIEW3D', depress=settings.is_enabled)
        row.popover("VIEW3D_PT_mouselook_navigation_header_popover", text="")

@addon.Panel(idname="VIEW3D_PT_mouselook_navigation_keymap_modifiers_popover", label="Modifiers", space_type='CONSOLE', region_type='WINDOW')
class KeymapModifiersPanel:
    key_names = {item.identifier: item.name for item in bpy.types.KeyMapItem.bl_rna.properties["key_modifier"].enum_items}
    
    @classmethod
    def get_label(cls, ark):
        modifiers = []
        
        if ark.any:
            modifiers.append("Any / no modifier")
        else:
            if ark.shift: modifiers.append("Shift")
            if ark.ctrl: modifiers.append("Ctrl")
            if ark.alt: modifiers.append("Alt")
            if ark.oskey: modifiers.append("Cmd")
        
        if ark.key_modifier != 'NONE':
            modifiers.append(cls.key_names[ark.key_modifier])
        
        return (" + ".join(modifiers) if modifiers else "No modifier")
    
    def draw(self, context):
        ark = getattr(context, "autoreg_keymap", None)
        if not ark: return
        
        layout = NestedLayout(self.layout)
        
        with layout.row(align=True):
            layout.prop(ark, "any", toggle=True)
            
            with layout.row(align=True)(active=(not ark.any)):
                layout.prop(ark, "shift", toggle=True)
                layout.prop(ark, "ctrl", toggle=True)
                layout.prop(ark, "alt", toggle=True)
                layout.prop(ark, "oskey", toggle=True)
        
        with layout.row():
            with layout.row()(alignment='LEFT', scale_x=0.7):
                layout.label(text="Key:")
            with layout.row(align=True):
                key_modifier_prop = BlRna(ark).properties["key_modifier"]
                layout.context_pointer_set("button_prop", key_modifier_prop)
                layout.context_pointer_set("button_pointer", ark)
                layout.prop(ark, "key_modifier", text="", event=True)
                layout.operator("mouselook_navigation.reset_property", text="", icon='X')

@addon.Operator.execute(idname="mouselook_navigation.reset_property", label="Reset", options={'INTERNAL'})
def reset_property(self, context):
    # For some reason, ui.unset_property_button operator doesn't work with custom context pointers
    bpy_obj = context.button_pointer
    prop_id = context.button_prop.identifier
    bpy_obj.property_unset(prop_id)
    BlUI.tag_redraw()

@addon.context_menu('APPEND')
def context_menu_draw(self, context):
    layout = self.layout
    
    if ConfigureShortcutKeys.poll(context):
        layout.separator()
        layout.operator("mouselook_navigation.configure_shortcut_keys")

@addon.PropertyGroup
class ShortcutConfigKey:
    invert: False | prop("Invert", "Trigger the shortcut when this key/event combination is NOT active")
    raw: "" | prop()
    key: 'NONE' | prop(items=[(item.identifier, item.name, item.description, item.icon, item.value)
                       for item in bpy.types.KeyMapItem.bl_rna.properties["type"].enum_items])
    modifier: "-" | prop(items=([("-", "-", "Not a modifier")] +
        [(k, InputKeyParser.names[k]) for k in InputKeyParser.modifiers.keys()]))

@addon.PropertyGroup
class ShortcutConfigEvent:
    event: 'NOTHING' | prop(items=[(item.identifier, item.name, item.description, item.icon, item.value)
        for item in bpy.types.KeyMapItem.bl_rna.properties["value"].enum_items])

@addon.Operator(idname="mouselook_navigation.configure_shortcut_keys", label="Configure", description="Configure shortcut keys", options={'INTERNAL'})
class ConfigureShortcutKeys:
    keys: [ShortcutConfigKey] | prop()
    events: [ShortcutConfigEvent] | prop()
    
    modes_map = {"value_type": 'KEYMAP', "key_modifier": 'MODIFIER'}
    
    @classmethod
    def poll(cls, context):
        button_prop = getattr(context, "button_prop", None)
        if button_prop is None: return False
        prop_id = getattr(button_prop, "identifier", None)
        if not isinstance(prop_id, str): return False
        
        button_pointer = getattr(context, "button_pointer", None)
        if isinstance(button_pointer, AutoRegKeymapInfo):
            return button_prop.identifier in ("value_type", "key_modifier")
        elif isinstance(button_pointer, MouselookNavigation_InputSettings):
            return button_prop.identifier.startswith("keys_")
        return False
    
    def invoke(self, context, event):
        button_pointer = context.button_pointer
        prop_id = context.button_prop.identifier
        
        self.invoke_key = ('INVOKE_KEY' if prop_id.startswith("keys_") else "")
        self.mode = self.modes_map.get(prop_id, 'SHORTCUT')
        key_infos, event_infos = InputKeyParser.parse(getattr(button_pointer, prop_id), self.invoke_key)
        
        for key_info in key_infos:
            config_key = self.keys.add()
            config_key.invert = key_info.invert
            config_key.raw = key_info.raw
            if key_info.type == 'EVENT_TYPE':
                config_key.key = key_info.normalized
            elif key_info.type == 'MODIFIER':
                config_key.modifier = key_info.normalized
        
        for event_info in event_infos:
            config_event = self.events.add()
            config_event.event = event_info.normalized or 'NOTHING'
        
        return context.window_manager.invoke_props_dialog(self)
    
    def execute(self, context):
        # Make sure we proceed only if invoke() was called earlier
        if not getattr(self, "mode", None): return {'CANCELLED'}
        
        return {'FINISHED'}
    
    def check(self, context):
        # TODO: return if the UI should be updated
        return False
    
    def draw(self, context):
        layout = self.layout
        
        #if self.mode == 'MODIFIER':
        
        for config_key in self.keys:
            row = layout.row(align=True)
            row.prop(config_key, "invert", text="", icon='REMOVE', toggle=True)
            row.prop(config_key, "raw", text="")
            row.prop(config_key, "key", text="", event=True)
            row.prop(config_key, "modifier", text="")
        
        for config_event in self.events:
            layout.prop(config_event, "event")

@addon.PropertyGroup
class NavigationDirectionFlip:
    orbit_x: False | prop()
    orbit_y: False | prop()
    dolly: False | prop()
    zoom_x: False | prop()
    zoom_y: False | prop()
    zoom_wheel: False | prop()
    
    def draw(self, layout):
        layout.label(text="Invert:")
        layout.prop(self, "orbit_x", toggle=True)
        layout.prop(self, "orbit_y", toggle=True)
        layout.prop(self, "dolly", toggle=True)
        layout.prop(self, "zoom_x", toggle=True)
        layout.prop(self, "zoom_y", toggle=True)
        layout.prop(self, "zoom_wheel", toggle=True)

@addon.Internal.Include
class InternalPG:
    is_enabled: True | prop("Enable/disable Mouselook Navigation", "")

@addon.Preferences.Include
class ThisAddonPreferences:
    prefs_tab: 'SETTINGS' | prop("Tab", "Which tab to show in addon preferences", items=[
        ('SETTINGS', "Settings", "General settings"),
        ('KEYMAPS', "Keymaps", "Setup auto-registered keymaps"),
        ('ABOUT', "About", "About"),
    ])
    
    show_in_shelf: True | prop("Show in shelf", f"Show a panel in the 3D view's shelf ('View' tab)")
    show_in_header: True | prop("Show in header", f"Show an icon in the 3D view's header")
    
    pass_through: False | prop("Non-blocking", "Other operators can be used while navigating")
    
    animation_fps: 50.0 | prop("Animation timer FPS", "Animation timer FPS")
    
    raycast_modes: BlEnums.context_modes | prop("Object modes",
        "Object modes in which geometry detection is enabled\n"+
        "(e.g. Raycast and Selection methods are slow when sculpting high-detail meshes, "+
        "so you might want to disable them for Sculpt mode)",
        items=[(mode_info.context, mode_info.name, mode_info.name) for mode_info in BlEnums.mode_infos.values()])
    
    adjust_multires: False | prop("Adjust Multires", "If enabled, and the 'Fast Navigate' option "+
        "in Blender's Sculpt menu is enabled, the sculpt resolution of Multires modifier will be "+
        "set to 1 during the navigation. However, for high-detail meshes, this will cause lag / "+
        "delays when starting or ending the navigation")
    
    show_crosshair: True | prop("Show Crosshair", "Crosshair visibility")
    show_focus: True | prop("Show Orbit Center", "Orbit Center visibility")
    show_zbrush_border: True | prop("Show ZBrush border", "ZBrush border visibility")
    use_blender_colors: True | prop("Use Blender's colors", "Use Blender's colors")
    color_crosshair_visible: Color() | prop("Crosshair (visible)", "Crosshair (visible) color")
    color_crosshair_obscured: Color() | prop("Crosshair (obscured)", "Crosshair (obscured) color")
    color_zbrush_border: Color() | prop("ZBrush border", "ZBrush border color")
    zbrush_border_scale: 5.0 | prop("ZBrush border scale", "Size of ZBrush border (relative to viewport size)",
        min=0.0, max=25.0, precision=1, subtype='PERCENTAGE')
    zbrush_border_min: 16 | prop("ZBrush border min size", "Minimal size of ZBrush border (in pixels)", min=0, subtype='PIXEL')
    
    def calc_zbrush_border_size(self, area, region):
        scale = self.zbrush_border_scale * 0.01
        abs_min = self.zbrush_border_min * BlUI.ui_scale()
        size_min = BlUI.calc_region_rect(area, region, overlap=False).size.min()
        return max(size_min * scale, abs_min)
    
    def get_color(self, attr_name):
        if self.use_blender_colors:
            try:
                return userprefs.themes[0].view_3d.view_overlay
            except:
                return Color((0,0,0))
        else:
            return getattr(self, attr_name)
    
    def calc_zbrush_mode(self):
        if self.is_using_universal_input_settings:
            return (self.universal_input_settings.zbrush_mode != 'NONE')
        return any((ark.input_settings.zbrush_mode != 'NONE') for ark in self.autoreg_keymaps)
    zbrush_mode: False | prop(get=calc_zbrush_mode)
    
    use_default_keymap: True | prop("Use default keymap", options={'HIDDEN'})
    autoreg_keymaps: [AutoRegKeymapInfo] | prop("Auto-registered keymaps", "Auto-registered keymaps")
    autoreg_keymap_id: 0 | prop("Keymap ID", "Keymap ID", min=0)
    use_universal_input_settings: True | prop("Universal", "Use same settings for each keymap")
    universal_input_settings: MouselookNavigation_InputSettings | prop()
    
    @property
    def is_using_universal_input_settings(self):
        return self.use_universal_input_settings or (len(self.autoreg_keymaps) == 0)
    
    zbrush_radius: 0 | prop("Geometry detection radius", "Minimal required distance (in pixels) to the nearest geometry", min=0, max=64, subtype='PIXEL')
    zbrush_method: 'ZBUFFER' | prop("Geometry detection method", "Which method to use to determine if mouse is over empty space", items=[
        ('RAYCAST', "Raycast", "WARNING: causes problems in Sculpt mode"),
        ('SELECTION', "Selection", "WARNING: causes problems in Sculpt mode"),
        ('ZBUFFER', "Z-buffer", "WARNING: may potentially crash Blender, if other addons attempt to use wm.redraw_timer() in the same frame"),
    ])
    
    flips: NavigationDirectionFlip | prop()
    
    zoom_speed_modifier: 1.0 | prop("Zoom speed", "Zooming speed")
    rotation_speed_modifier: 1.0 | prop("Rotation speed", "Rotation speed")
    fps_speed_modifier: 1.0 | prop("FPS speed", "FPS movement speed")
    fps_horizontal: False | prop("FPS horizontal", "Force forward/backward to be in horizontal plane, and up/down to be vertical")
    zoom_to_selection: True | prop("Zoom to selection", "Zoom to selection when Orbit Around Selection is enabled")
    trackball_mode: 'WRAPPED' | prop("Trackball mode", "Rotation algorithm used in trackball mode", items=[
        ('BLENDER', 'Blender', 'Blender (buggy!)', 'ERROR'),
        ('WRAPPED', 'Wrapped'),
        ('CENTER', 'Center'),
    ])
    rotation_snap_subdivs: 2 | prop("Orbit snap subdivs", "Intermediate angles used when snapping (1: 90°, 2: 45°, 3: 30°, etc.)", min=1)
    rotation_snap_autoperspective: True | prop("Orbit snap->ortho", "If Auto Perspective is enabled, rotation snapping will automatically switch the view to Ortho")
    autolevel_trackball: False | prop("Trackball Autolevel", "Autolevel in Trackball mode")
    autolevel_trackball_up: False | prop("Trackball Autolevel up", "Try to autolevel 'upright' in Trackball mode")
    autolevel_speed_modifier: 0.0 | prop("Autolevel speed", "Autoleveling speed", min=0.0)
    
    def _is_trackball_get(self):
        input_prefs = bpy.context.preferences.inputs
        return input_prefs.view_rotate_method == 'TRACKBALL'
    def _is_trackball_set(self, value):
        value = ('TRACKBALL' if value else 'TURNTABLE')
        input_prefs = bpy.context.preferences.inputs
        if input_prefs.view_rotate_method != value: input_prefs.view_rotate_method = value
    show_trackball: False | prop("Show the trackball/turntable switch", "Display a trackball/turntable indicator in the header")
    is_trackball: False | prop("Use Trackball orbit", "Use the Trackball orbiting method", get=_is_trackball_get, set=_is_trackball_set)
    
    auto_trackball: False | prop("Auto Trackball/Turntable", "Enable automatic switching between Trackball and Turntable in certain object modes")
    auto_trackball_modes: {} | prop("Auto Trackball modes", "In which object modes to use Trackball",
        items=[(mode_name, BlEnums.get_mode_name(mode_name), "") for mode_name in sorted(BlEnums.context_modes)])
    
    def draw(self, context):
        layout = NestedLayout(self.layout)
        
        with layout.row():
            layout.prop_tabs_enum(self, "prefs_tab")
        
        if self.prefs_tab == 'ABOUT':
            self.draw_about(context, layout)
        elif self.prefs_tab == 'KEYMAPS':
            self.draw_autoreg_keymaps(context, layout)
        else:
            self.draw_settings(context, layout)
    
    def draw_about(self, context, layout):
        with layout.row():
            with layout.column():
                layout.label(text="Official:")
                layout.operator("wm.url_open", text="BATCH TOOLS™ 2 Store").url = "https://www.moth3r.com"
                layout.operator("wm.url_open", text="Documentation").url = "http://gum.co/mouselook"
            with layout.column():
                layout.label(text="Recommended:")
                layout.operator("wm.url_open", text="MasterXeon1001 addons").url = "https://gumroad.com/masterxeon1001"
                layout.operator("wm.url_open", text="MACHIN3 tools").url = "https://machin3.io/"
    
    def draw_settings(self, context, layout):
        with layout.box():
            with layout.row():
                layout.label(text="UI:")
                with layout.row()(alignment='RIGHT'):
                    layout.prop(self, "show_in_shelf", toggle=True)
                    layout.prop(self, "show_in_header", toggle=True)
                    layout.prop(self, "use_blender_colors", toggle=True)
            
            with layout.row():
                with layout.box():
                    with layout.row():
                        layout.prop(self, "show_crosshair", text="Crosshair", toggle=True)
                        with layout.row()(active=self.show_crosshair):
                            layout.prop(self, "show_focus", text="Orbit Center", toggle=True)
                    with layout.column()(active=self.show_crosshair):
                        layout.row().prop(self, "color_crosshair_visible", text="Visible")
                        layout.separator(factor=0.5)
                        layout.row().prop(self, "color_crosshair_obscured", text="Obscured")
                
                with layout.box():
                    layout.prop(self, "show_zbrush_border", text="ZBrush border", toggle=True)
                    with layout.row()(active=self.show_zbrush_border):
                        layout.prop(self, "zbrush_border_scale", text="Scale", slider=True)
                        layout.prop(self, "zbrush_border_min", text="Min")
                    with layout.row()(active=self.show_zbrush_border):
                        layout.row().prop(self, "color_zbrush_border", text="Color")
        
        with layout.box():
            with layout.row():
                layout.label(text="Behavior:")
                layout.prop(self, "pass_through", toggle=True)
                layout.prop(self, "animation_fps", text="Framerate")
            with layout.row():
                layout.prop(self, "fps_horizontal", toggle=True)
                layout.prop(self, "zoom_to_selection", toggle=True)
                layout.prop(self, "adjust_multires", toggle=True)
            
            with layout.column(align=True):
                with layout.row(align=True):
                    layout.prop(self, "zoom_speed_modifier", text="Zoom speed")
                    layout.prop(self, "rotation_speed_modifier", text="Rotation speed")
                with layout.row(align=True):
                    layout.prop(self, "fps_speed_modifier", text="Movement speed")
                    layout.prop(self, "autolevel_speed_modifier", text="Autolevel speed")
            
            with layout.row()(alignment='LEFT'):
                layout.label(text="Geometry detection:")
                with layout.row():
                    layout.prop(self, "zbrush_method", text="")
                    layout.prop_menu_enum(self, "raycast_modes", text="Object modes")
                    with layout.row()(scale_x=0.85):
                        layout.prop(self, "zbrush_radius", text="Radius")
            
            with layout.row()(alignment='LEFT'):
                layout.label(text="Orbit snap:")
                layout.prop(self, "rotation_snap_autoperspective", text="To Ortho", toggle=True)
                with layout.row()(scale_x=0.85):
                    layout.prop(self, "rotation_snap_subdivs", text="Subdivs")
            
            with layout.row()(alignment='LEFT'):
                layout.label(text="Trackball:")
                layout.prop(*settings("trackball_mode"), text="")
                with layout.row(align=True):
                    layout.prop(*settings("autolevel_trackball"), text="Autolevel", toggle=True)
                    with layout.row(align=True)(active=settings.autolevel_trackball):
                        layout.prop(*settings("autolevel_trackball_up"), text="", icon='EMPTY_SINGLE_ARROW', toggle=True)
                with layout.row(align=True)(alignment='RIGHT'):
                    layout.prop(self, "auto_trackball", text="Auto switch", toggle=True)
                    layout.prop_menu_enum(self, "auto_trackball_modes", text="", icon='TRIA_DOWN')
                layout.prop(self, "show_trackball", text="", icon='HIDE_OFF', toggle=True)
    
    def draw_autoreg_keymaps(self, context, layout):
        is_using_universal_input_settings = self.is_using_universal_input_settings
        
        with layout.row(align=True):
            self.flips.draw(layout)
        
        with layout.row():
            layout.operator("mouselook_navigation.autoreg_keymap_add", text="Add Keymap", icon='ADD')
            layout.operator("mouselook_navigation.autoreg_keymaps_update", text="Update Keymaps", icon='FILE_REFRESH')
            AutoregKeymapPreset.draw_popup(layout, text="Load Preset", icon='PRESET')
        
        with layout.column(align=True):
            autoreg_keymaps = self.autoreg_keymaps
            for i, ark in enumerate(autoreg_keymaps):
                with layout.box():
                    with layout.column(align=True):
                        with layout.row():
                            icon = (('PROP_CON' if ark.is_current else 'PROP_ON') if not is_using_universal_input_settings else 'PROP_OFF')
                            layout.prop(ark, "is_current", text="", icon=icon, icon_only=True, toggle=True, emboss=False)
                            with layout.row(align=True):
                                with layout.row()(alignment='LEFT'):
                                    layout.context_pointer_set("autoreg_keymap", ark)
                                    layout.popover(KeymapModifiersPanel.bl_idname, text=KeymapModifiersPanel.get_label(ark))
                                layout.label(icon='ADD')
                                with layout.row():
                                    layout.alert = not InputKeyParser.validate(ark.value_type, can_invert=False)
                                    layout.prop(ark, "value_type", text="")
                            with layout.row()(alignment='LEFT'):
                                layout.prop_menu_enum(ark, "keymaps", text="Keymaps")
                            layout.operator("mouselook_navigation.autoreg_keymap_remove", text="", icon='X').index = i
                        
                        layout.separator(factor=0.5)
                        
                        with layout.row():
                            layout.prop(ark, "insert_after", text="")
                            layout.label(icon='ARROW_LEFTRIGHT')
                            layout.prop(ark, "insert_before", text="")
        
        with layout.box():
            if is_using_universal_input_settings:
                input_settings = self.universal_input_settings
                input_settings.draw(layout, None)
            else:
                autoreg_keymap_id = min(self.autoreg_keymap_id, len(self.autoreg_keymaps)-1)
                input_settings = self.autoreg_keymaps[autoreg_keymap_id].input_settings
                input_settings.draw(layout, self.universal_input_settings)

def register():
    addon.register()
    
    addon.draw_handler_add(bpy.types.SpaceView3D, draw_callback_px, (None, None), 'WINDOW', 'POST_PIXEL')
    
    addon.runtime.keymaps_initialized = False

def unregister():
    update_keymaps(False)
    
    addon.unregister()
