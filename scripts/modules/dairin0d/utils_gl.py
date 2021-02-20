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

#============================================================================#

'''
Speed tips:
* Static methods are faster than instance/class methods
  Seems like argument passing is a relatively expensive operation
* Closure dictionary lookup with constant key is faster:
    # non-arguments are assumed to be closure-bound objects
    def f(enum): g(enum)
    f(GL_BLEND) # bgl.GL_BLEND is even slower
    def f(enum): g(enums[enum])
    f('BLEND') # faster
'''

# Convention:
# UPPER_CASE is for enabled/disabled capabilities and constants
# CamelCase is for parameters/functions
# lower_case is for extra functionality

# Alternative:
# NAME/Name for set
# NAME_/Name_ for get
# or: get_Name, set_Name to not conflict with properties

def initialize():
    import math
    from collections import namedtuple
    
    from mathutils import Color, Vector, Matrix, Quaternion, Euler
    
    import bgl
    import blf
    import gpu
    
    from bgl import (
        Buffer,
        GL_TRUE, GL_FALSE, glIsEnabled, glEnable, glDisable,
        GL_INT, glGetIntegerv,
        GL_FLOAT, glGetFloatv,
        GL_DEPTH_COMPONENT, glReadPixels,
    )
    
    from gpu.types import GPUBatch, GPUIndexBuf, GPUOffScreen, GPUShader, GPUVertBuf, GPUVertFormat
    
    bgl_names = dir(bgl)
    
    shader_from_builtin = gpu.shader.from_builtin
    
    blf_options = {'ROTATION':blf.ROTATION, 'CLIPPING':blf.CLIPPING, 'SHADOW':blf.SHADOW,
        'KERNING_DEFAULT':blf.KERNING_DEFAULT, 'WORD_WRAP':blf.WORD_WRAP, 'MONOCHROME':blf.MONOCHROME}
    
    blf_load = blf.load
    blf_unload = blf.unload
    blf_enable = blf.enable
    blf_disable = blf.disable
    blf_shadow = blf.shadow
    blf_shadow_offset = blf.shadow_offset
    blf_color = blf.color
    blf_position = blf.position
    blf_rotation = blf.rotation
    blf_size = blf.size
    blf_clipping = blf.clipping
    blf_aspect = blf.aspect
    blf_dimensions = blf.dimensions
    blf_draw = blf.draw
    blf_word_wrap = blf.word_wrap
    
    class StateRestorator:
        __slots__ = ("state", "target")
        
        def __init__(self, target, args, kwargs):
            state = {}
            for k in args:
                state[k] = getattr(target, k)
            for k, v in kwargs.items():
                state[k] = getattr(target, k)
                setattr(target, k, v)
            self.state = state
            self.target = target
        
        def restore(self):
            target = self.target
            for k, v in self.state.items():
                setattr(target, k, v)
        
        def __enter__(self):
            return self
        
        def __exit__(self, type, value, traceback):
            self.restore()
    
    class CGL:
        def __call__(self, *args, **kwargs):
            return StateRestorator(self, args, kwargs)
        
        # Adapted from gpu_extras.batch.batch_for_shader()
        @staticmethod
        def batch(*args, **attr_data):
            """
            Positional arguments: vbo format | shader | built-in shader name, primitive type, [indices]
            Keyword arguments: data for the corresponding shader attributes
            """
            
            arg_count = len(args)
            if (arg_count < 2) or (arg_count > 3):
                raise TypeError(f"batch() takes from 2 to 3 positional arguments but {arg_count} were given")
            
            vbo_len = 0
            for data in attr_data.values():
                if not data: continue
                data_len = len(data)
                if data_len == vbo_len: continue
                if vbo_len == 0:
                    vbo_len = data_len
                else:
                    raise ValueError("Length mismatch for vertex attribute data")
            
            vbo_format = args[0]
            if isinstance(vbo_format, str): vbo_format = shader_from_builtin(vbo_format)
            if isinstance(vbo_format, GPUShader): vbo_format = vbo_format.format_calc()
            
            primitive_type = args[1]
            
            indices = (args[2] if arg_count > 2 else None)
            
            vbo = GPUVertBuf(vbo_format, vbo_len)
            for id, data in attr_data.items():
                vbo.attr_fill(id, data)
            
            if indices is None:
                return GPUBatch(type=primitive_type, buf=vbo)
            else:
                ibo = GPUIndexBuf(type=primitive_type, seq=indices)
                return GPUBatch(type=primitive_type, buf=vbo, elem=ibo)
        
        @staticmethod
        def read_zbuffer(xy, wh=(1, 1), centered=False, src=None):
            if isinstance(wh, (int, float)):
                wh = (wh, wh)
            elif len(wh) < 2:
                wh = (wh[0], wh[0])
            
            x, y, w, h = int(xy[0]), int(xy[1]), int(wh[0]), int(wh[1])
            
            if centered:
                x -= w // 2
                y -= h // 2
            
            buf_size = w*h
            
            if src is None:
                # xy is in window coordinates!
                zbuf = Buffer(GL_FLOAT, [buf_size])
                glReadPixels(x, y, w, h, GL_DEPTH_COMPONENT, GL_FLOAT, zbuf)
            else:
                src, w0, h0 = src
                template = [0.0] * buf_size
                for dy in range(h):
                    y0 = min(max(y + dy, 0), h0-1)
                    for dx in range(w):
                        x0 = min(max(x + dx, 0), w0-1)
                        i0 = x0 + y0 * w0
                        i1 = dx + dy * w
                        template[i1] = src[i0]
                zbuf = Buffer(GL_FLOAT, [buf_size], template)
            
            return zbuf

    cgl = CGL()
    
    # ========== TEXT ========== #
    
    class TextWrapper:
        # dimensions & wrapping calculation
        @classmethod
        def dimensions(cls, text, font=0):
            return blf_dimensions(font, text)
        
        @classmethod
        def _split_word(cls, width, x, max_x, word, lines, font):
            line = ""
            
            for c in word:
                x_dx = x + blf_dimensions(font, line+c)[0]
                
                if x_dx > width:
                    x_dx = x + blf_dimensions(font, line)[0]
                    lines.append(line)
                    line = c
                    x = 0
                else:
                    line += c
                
                max_x = max(x_dx, max_x)
            
            return line, x, max_x

        @classmethod
        def _split_line(cls, width, x, max_x, line, lines, font):
            words = line.split(" ")
            line = ""
            
            for word in words:
                c = (word if not line else " " + word)
                x_dx = x + blf_dimensions(font, line+c)[0]
                
                if x_dx > width:
                    x_dx = x + blf_dimensions(font, line)[0]
                    if not line:
                        line, x, max_x = cls._split_word(width, x, max_x, word, lines, font)
                    else:
                        lines.append(line)
                        line, x, max_x = cls._split_word(width, 0, max_x, word, lines, font)
                    x = 0
                else:
                    line += c
                
                max_x = max(x_dx, max_x)
            
            if line: lines.append(line)
            
            return max_x

        @classmethod
        def split_text(cls, width, x, max_x, text, lines, font=0):
            if width is None: width = math.inf
            
            for line in text.splitlines():
                if not line:
                    lines.append("")
                else:
                    max_x = cls._split_line(width, x, max_x, line, lines, font)
                x = 0
            
            return max_x

        @classmethod
        def wrap_text(cls, text, width, indent=0, font=0):
            """
            Splits text into lines that don't exceed the given width.
            text -- the text
            width -- the width the text should fit into
            font -- the id of the typeface as returned by blf.load(). Defaults to 0 (the default font)
            indent -- the indent of the paragraphs. Defaults to 0
            Returns: lines, size
            lines -- the list of the resulting lines
            size -- actual (width, height) of these lines
            """
            
            if width is None: width = math.inf
            
            lines = []
            max_x = 0
            for line in text.splitlines():
                if not line:
                    lines.append("")
                else:
                    max_x = cls._split_line(width, indent, max_x, line, lines, font)
            
            line_height = blf_dimensions(font, "Ig")[1]
            
            return lines, (max_x, len(lines)*line_height)
    
    class BatchedText:
        __slots__ = ("font", "pieces", "size")
        
        def __init__(self, font, pieces, size):
            self.font = font
            self.pieces = pieces
            self.size = size
        
        def draw(self, pos, origin=None):
            x = pos[0]
            y = pos[1]
            z = (pos[2] if len(pos) > 2 else 0)
            
            if origin:
                x -= self.size[0] * origin[0]
                y -= self.size[1] * origin[1]
            
            prev_blend = cgl.BLEND
            prev_polygon_smooth = cgl.POLYGON_SMOOTH
            
            cgl.BLEND = True
            cgl.POLYGON_SMOOTH = False
            
            font = self.font
            x0, y0 = round(x), round(y)
            for txt, x, y in self.pieces:
                blf_position(font, x0+x, y0+y, z)
                blf_draw(font, txt)
            
            # Note: blf_draw() resets GL_BLEND (and GL_POLYGON_SMOOTH
            # since Blender 2.91), so we have to restore them anyway
            
            cgl.POLYGON_SMOOTH = prev_polygon_smooth
            cgl.BLEND = prev_blend
    
    class Text:
        font = 0 # 0 is the default font
        
        # load / unload
        def load(self, filename, size=None, dpi=72):
            font = blf_load(filename)
            if size is not None: blf_size(font, int(size), dpi)
            return font
        def unload(self, filename):
            blf_unload(filename)
        
        # enable / disable options
        def enable(self, option):
            blf_enable(self.font, blf_options[option])
        def disable(self, option):
            blf_disable(self.font, blf_options[option])
        
        # set shadow
        def shadow(self, level, r, g, b, a):
            blf_shadow(self.font, level, r, g, b, a)
        def shadow_offset(self, x, y):
            blf_shadow_offset(self.font, x, y)
        
        # set position / rotation / size / color
        def position(self, x, y, z=0.0):
            blf_position(self.font, x, y, z)
        def rotation(self, angle):
            blf_rotation(self.font, angle)
        def size(self, size, dpi=72):
            blf_size(self.font, int(size), dpi)
        def color(self, r, g, b, a=1.0):
            blf_color(self.font, r, g, b, a)
        
        # set clipping / aspect
        def clipping(self, xmin, ymin, xmax, ymax):
            blf_clipping(self.font, xmin, ymin, xmax, ymax)
        def aspect(self, aspect):
            blf_aspect(self.font, aspect)
        
        def compile(self, text, width=None, alignment=None, spacing=1.0):
            font = self.font
            
            line_height = blf_dimensions(font, "Ig")[1]
            topline = blf_dimensions(font, "I")[1]
            
            lines, size = TextWrapper.wrap_text(text, width, font=font)
            
            w, h = size[0], size[1] * abs(spacing)
            
            size = (w, h)
            pieces = []
            
            if (alignment in (None, 'LEFT')): alignment = 0.0
            elif (alignment == 'CENTER'): alignment = 0.5
            elif (alignment == 'RIGHT'): alignment = 1.0
            
            # blf text origin is at lower left corner, and +Y is "up"
            # But since text is usually read from top to bottom,
            # consider positive spacing to be "down".
            if spacing > 0: lines = reversed(lines)
            
            y_step = line_height * abs(spacing)
            
            x, y = 0, 0
            for line in lines:
                x = (w - blf_dimensions(font, line)[0]) * alignment
                pieces.append((line, round(x), round(y)))
                y += y_step
            
            return BatchedText(font, pieces, size)
        
        def draw(self, text, pos=None, origin=None, width=None, alignment=None, spacing=1.0):
            if pos is None:
                # if position is not specified, other calculations cannot be performed
                blf_draw(self.font, text)
                return None
            
            batched = self.compile(text, width, alignment, spacing)
            batched.draw(pos, origin)
            return batched
    
    cgl.text = Text()
    
    # ========== GL API ========== #
    
    def Cap(name, doc=""):
        pname = name[3:]
        if hasattr(CGL, pname): return
        
        state_id = getattr(bgl, name, None)
        if state_id is None: return
        
        class Descriptor:
            __doc__ = doc
            def __get__(self, instance, owner):
                return bool(glIsEnabled(state_id))
            def __set__(self, instance, value):
                (glEnable if value else glDisable)(state_id)
        
        setattr(CGL, pname, Descriptor())
    
    def add_descriptor(name, getter, setter, doc=""):
        #Descriptor = type(name+"_Descriptor", (), {"__doc__":doc, "__get__":getter, "__set__":setter})
        class Descriptor:
            __doc__ = doc
            __get__ = getter
            __set__ = setter
        setattr(CGL, name, Descriptor())
    
    def map_enum(*names):
        enum_k2v = {}
        enum_v2k = {}
        for name in names:
            if name.startswith("GL_"): name = name[3:]
            value = getattr(bgl, "GL_"+name, None)
            if value is None: continue # bgl does not always expose everything
            enum_k2v[name] = value
            enum_v2k[value] = name
        return enum_k2v, enum_v2k
    
    # OpenGL 4-4.5 level capabilities
    # https://www.khronos.org/registry/OpenGL-Refpages/gl4/html/glEnable.xhtml
    Cap('GL_BLEND')
    Cap('GL_CLIP_DISTANCE0')
    Cap('GL_CLIP_DISTANCE1')
    Cap('GL_CLIP_DISTANCE2')
    Cap('GL_CLIP_DISTANCE3')
    Cap('GL_CLIP_DISTANCE4')
    Cap('GL_CLIP_DISTANCE5')
    Cap('GL_COLOR_LOGIC_OP')
    Cap('GL_CULL_FACE')
    Cap('GL_DEBUG_OUTPUT')
    Cap('GL_DEBUG_OUTPUT_SYNCHRONOUS')
    Cap('GL_DEPTH_CLAMP')
    Cap('GL_DEPTH_TEST')
    Cap('GL_DITHER')
    Cap('GL_FRAMEBUFFER_SRGB')
    Cap('GL_LINE_SMOOTH')
    Cap('GL_MULTISAMPLE')
    Cap('GL_POLYGON_OFFSET_FILL')
    Cap('GL_POLYGON_OFFSET_LINE')
    Cap('GL_POLYGON_OFFSET_POINT')
    Cap('GL_POLYGON_SMOOTH')
    Cap('GL_PRIMITIVE_RESTART')
    Cap('GL_PRIMITIVE_RESTART_FIXED_INDEX')
    Cap('GL_RASTERIZER_DISCARD')
    Cap('GL_SAMPLE_ALPHA_TO_COVERAGE')
    Cap('GL_SAMPLE_ALPHA_TO_ONE')
    Cap('GL_SAMPLE_COVERAGE')
    Cap('GL_SAMPLE_SHADING')
    Cap('GL_SAMPLE_MASK')
    Cap('GL_SCISSOR_TEST')
    Cap('GL_STENCIL_TEST')
    Cap('GL_TEXTURE_CUBE_MAP_SEAMLESS')
    Cap('GL_PROGRAM_POINT_SIZE') # old name: GL_VERTEX_PROGRAM_POINT_SIZE
    
    range4 = tuple(range(4))
    def matrix_to_buffer(matrix, dtype=GL_FLOAT):
        return Buffer(dtype, 16, [matrix[i][j] for i in range4 for j in range4])
    def buffer_to_matrix(buf):
        return Matrix((buf[0:4], buf[4:8], buf[8:12], buf[12:16]))
    
    cgl.matrix_to_buffer = staticmethod(matrix_to_buffer)
    cgl.buffer_to_matrix = staticmethod(buffer_to_matrix)
    
    int1buf0 = Buffer(GL_INT, 1)
    int1buf1 = Buffer(GL_INT, 1)
    int1buf2 = Buffer(GL_INT, 1)
    int1buf3 = Buffer(GL_INT, 1)
    int4buf0 = Buffer(GL_INT, 4)
    float1buf0 = Buffer(GL_FLOAT, 1)
    float1buf1 = Buffer(GL_FLOAT, 1)
    float2buf0 = Buffer(GL_FLOAT, 2)
    float4buf0 = Buffer(GL_FLOAT, 4)
    matrixbuf0 = Buffer(GL_FLOAT, 16)
    
    if hasattr(bgl, "glLineWidth"):
        from bgl import GL_LINE_WIDTH, glLineWidth
        def _get(self, instance, owner):
            glGetFloatv(GL_LINE_WIDTH, float1buf0)
            return float1buf0[0]
        def _set(self, instance, value):
            glLineWidth(value)
        add_descriptor("LineWidth", _get, _set)
    
    if hasattr(bgl, "glBlendColor"):
        from bgl import glBlendColor
        GL_BLEND_COLOR = 32773 # absent in bgl (at least in Blender 2.80 - 2.91)
        def _get(self, instance, owner):
            glGetFloatv(GL_BLEND_COLOR, float4buf0)
            return Vector(float4buf0)
        def _set(self, instance, value):
            glBlendColor(float(value[0]), float(value[1]), float(value[2]), float(value[3]))
        add_descriptor("BlendColor", _get, _set)
    
    blend_funcs_k2v, blend_funcs_v2k = map_enum(
        'GL_ZERO', 'GL_ONE',
        'GL_SRC_COLOR', 'GL_ONE_MINUS_SRC_COLOR',
        'GL_DST_COLOR', 'GL_ONE_MINUS_DST_COLOR',
        'GL_SRC_ALPHA', 'GL_ONE_MINUS_SRC_ALPHA',
        'GL_DST_ALPHA', 'GL_ONE_MINUS_DST_ALPHA',
        'GL_CONSTANT_COLOR', 'GL_ONE_MINUS_CONSTANT_COLOR',
        'GL_CONSTANT_ALPHA', 'GL_ONE_MINUS_CONSTANT_ALPHA',
        'GL_SRC_ALPHA_SATURATE',
        'GL_SRC1_COLOR', 'GL_ONE_MINUS_SRC1_COLOR',
        'GL_SRC1_ALPHA', 'GL_ONE_MINUS_SRC1_ALPHA',
    )
    
    if hasattr(bgl, "glBlendFunc"):
        from bgl import GL_BLEND_SRC_RGB, GL_BLEND_DST_RGB, glBlendFunc
        BlendFunc = namedtuple("BlendFunc", ("src", "dst"))
        def _get(self, instance, owner):
            glGetIntegerv(GL_BLEND_SRC_RGB, int1buf0)
            glGetIntegerv(GL_BLEND_DST_RGB, int1buf1)
            return BlendFunc(blend_funcs_v2k[int1buf0[0]], blend_funcs_v2k[int1buf1[0]])
        def _set(self, instance, value):
            glBlendFunc(blend_funcs_k2v[value[0]], blend_funcs_k2v[value[1]])
        add_descriptor("BlendFunc", _get, _set)
    
    if hasattr(bgl, "glBlendFuncSeparate"):
        # Note: glBlendFuncSeparate is not present in bgl (at least in Blender 2.80 - 2.91)
        from bgl import GL_BLEND_SRC_RGB, GL_BLEND_DST_RGB, GL_BLEND_SRC_ALPHA, GL_BLEND_DST_ALPHA, glBlendFuncSeparate
        BlendFuncSeparate = namedtuple("BlendFuncSeparate", ("src_rgb", "dst_rgb", "src_alpha", "dst_alpha"))
        def _get(self, instance, owner):
            glGetIntegerv(GL_BLEND_SRC_RGB, int1buf0)
            glGetIntegerv(GL_BLEND_DST_RGB, int1buf1)
            glGetIntegerv(GL_BLEND_SRC_ALPHA, int1buf2)
            glGetIntegerv(GL_BLEND_DST_ALPHA, int1buf3)
            return BlendFuncSeparate(blend_funcs_v2k[int1buf0[0]], blend_funcs_v2k[int1buf1[0]],
                                     blend_funcs_v2k[int1buf2[0]], blend_funcs_v2k[int1buf3[0]])
        def _set(self, instance, value):
            glBlendFuncSeparate(blend_funcs_k2v[value[0]], blend_funcs_k2v[value[1]],
                                blend_funcs_k2v[value[2]], blend_funcs_k2v[value[3]])
        add_descriptor("BlendFuncSeparate", _get, _set)
    
    blend_equations_k2v, blend_equations_v2k = map_enum(
        'GL_FUNC_ADD', 'GL_FUNC_SUBTRACT', 'GL_FUNC_REVERSE_SUBTRACT', 'GL_MIN', 'GL_MAX',
    )
    
    if hasattr(bgl, "glBlendEquation"):
        from bgl import GL_BLEND_EQUATION_RGB, glBlendEquation
        def _get(self, instance, owner):
            glGetIntegerv(GL_BLEND_EQUATION_RGB, int1buf0)
            return blend_equations_v2k[int1buf0[0]]
        def _set(self, instance, value):
            glBlendEquation(blend_equations_k2v[value])
        add_descriptor("BlendEquation", _get, _set)
    
    if hasattr(bgl, "glBlendEquationSeparate"):
        from bgl import GL_BLEND_EQUATION_RGB, GL_BLEND_EQUATION_ALPHA, glBlendEquationSeparate
        BlendEquationSeparate = namedtuple("BlendEquationSeparate", ("mode_rgb", "mode_alpha"))
        def _get(self, instance, owner):
            glGetIntegerv(GL_BLEND_EQUATION_RGB, int1buf0)
            glGetIntegerv(GL_BLEND_EQUATION_ALPHA, int1buf1)
            return BlendEquationSeparate(blend_equations_v2k[int1buf0[0]], blend_equations_v2k[int1buf1[0]])
        def _set(self, instance, value):
            glBlendEquationSeparate(blend_equations_k2v[value[0]], blend_equations_k2v[value[1]])
        add_descriptor("BlendEquationSeparate", _get, _set)
    
    depth_funcs_k2v, depth_funcs_v2k = map_enum(
        'GL_NEVER', 'GL_LESS', 'GL_EQUAL', 'GL_LEQUAL', 'GL_GREATER', 'GL_NOTEQUAL', 'GL_GEQUAL', 'GL_ALWAYS'
    )
    
    if hasattr(bgl, "glDepthFunc"):
        from bgl import GL_DEPTH_FUNC, glDepthFunc
        def _get(self, instance, owner):
            glGetIntegerv(GL_DEPTH_FUNC, int1buf0)
            return depth_funcs_v2k[int1buf0[0]]
        def _set(self, instance, value):
            glDepthFunc(depth_funcs_k2v[value])
        add_descriptor("DepthFunc", _get, _set)
    
    if hasattr(bgl, "glDepthMask"):
        from bgl import GL_DEPTH_WRITEMASK, glDepthMask
        def _get(self, instance, owner):
            glGetIntegerv(GL_DEPTH_WRITEMASK, int1buf0)
            return bool(int1buf0[0])
        def _set(self, instance, value):
            glDepthMask(GL_TRUE if value else GL_FALSE)
        add_descriptor("DepthMask", _get, _set)
    
    if hasattr(bgl, "glDepthRange"):
        from bgl import GL_DEPTH_RANGE, glDepthRange
        DepthRange = namedtuple("DepthRange", ("near", "far"))
        def _get(self, instance, owner):
            glGetFloatv(GL_DEPTH_RANGE, float2buf0)
            return DepthRange(*float2buf0)
        def _set(self, instance, value):
            glDepthRange(float(value[0]), float(value[1]))
        add_descriptor("DepthRange", _get, _set)
    
    if hasattr(bgl, "glColorMask"):
        from bgl import GL_COLOR_WRITEMASK, glColorMask
        def _get(self, instance, owner):
            glGetIntegerv(GL_COLOR_WRITEMASK, int4buf0)
            return bool(int4buf0[0]), bool(int4buf0[1]), bool(int4buf0[2]), bool(int4buf0[3])
        def _set(self, instance, value):
            glColorMask(*(GL_TRUE if item else GL_FALSE for item in value))
        add_descriptor("ColorMask", _get, _set)
    
    if hasattr(bgl, "glPolygonOffset"):
        from bgl import GL_POLYGON_OFFSET_FACTOR, GL_POLYGON_OFFSET_UNITS, glPolygonOffset
        PolygonOffset = namedtuple("PolygonOffset", ("factor", "units"))
        def _get(self, instance, owner):
            glGetFloatv(GL_POLYGON_OFFSET_FACTOR, float2buf0)
            glGetFloatv(GL_POLYGON_OFFSET_UNITS, float2buf1)
            return PolygonOffset(float2buf0[0], float2buf1[0])
        def _set(self, instance, value):
            glPolygonOffset(float(value[0]), float(value[1]))
        add_descriptor("PolygonOffset", _get, _set)
    
    if hasattr(bgl, "glScissor"):
        from bgl import GL_SCISSOR_BOX, glScissor
        def _get(self, instance, owner):
            glGetIntegerv(GL_SCISSOR_BOX, int4buf0)
            return tuple(int4buf0)
        def _set(self, instance, value):
            glScissor(int(value[0]), int(value[1]), int(value[2]), int(value[3]))
        add_descriptor("Scissor", _get, _set)
    
    if hasattr(bgl, "glActiveTexture"):
        textures_k2v, textures_v2k = {}, {}
        prefix = "GL_TEXTURE"
        prefix_len = len(prefix)
        for name in bgl_names:
            if not name.startswith(prefix): continue
            tex_id = name[prefix_len:]
            if not tex_id.isdigit(): continue
            tex_id = int(tex_id)
            value = getattr(bgl, name)
            textures_k2v[tex_id] = value
            textures_v2k[value] = tex_id
        
        from bgl import GL_ACTIVE_TEXTURE, glActiveTexture
        def _get(self, instance, owner):
            glGetIntegerv(GL_ACTIVE_TEXTURE, int1buf0)
            return textures_v2k[int1buf0[0]]
        def _set(self, instance, value):
            glActiveTexture(textures_k2v[value])
        add_descriptor("ActiveTexture", _get, _set)
    
    return {"cgl":cgl, "TextWrapper":TextWrapper}

globals().update(initialize())
del initialize
