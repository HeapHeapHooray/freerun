"""GLSL shaders for the Freerun piano visualizer."""

# Unit quad vertex shader shared by full-screen background
BG_VERTEX_SHADER = """
#version 330 core
in vec2 in_vert;
out vec2 uv;
void main() {
    uv = in_vert;
    gl_Position = vec4(in_vert * 2.0 - 1.0, 0.0, 1.0);
}
"""

BG_FRAGMENT_SHADER = """
#version 330 core
in vec2 uv;
out vec4 fragColor;
uniform float u_hit_y;
uniform float u_num_white_keys;

void main() {
    // Sleek dark gradient background
    vec3 top_col = vec3(0.04, 0.045, 0.065);
    vec3 bot_col = vec3(0.075, 0.08, 0.11);
    vec3 col = mix(bot_col, top_col, uv.y);
    
    // Subtle lane guide stripes above the keyboard
    if (uv.y >= u_hit_y) {
        float lane = fract(uv.x * u_num_white_keys);
        float line = smoothstep(0.03, 0.0, lane) + smoothstep(0.97, 1.0, lane);
        col += vec3(0.012, 0.015, 0.022) * line;
    }
    
    fragColor = vec4(col, 1.0);
}
"""

# White keys instanced shader
WK_VERTEX_SHADER = """
#version 330 core
in vec2 in_vert; // (0,0) to (1,1)
in vec4 in_rect; // x, y, w, h in NDC
in vec4 in_color; // active key color
in float in_pressed; // 0.0 to 1.0

out vec2 uv;
out vec4 v_rect;
out vec4 v_color;
out float v_pressed;

void main() {
    uv = in_vert;
    v_rect = in_rect;
    v_color = in_color;
    v_pressed = in_pressed;
    vec2 pos = in_rect.xy + in_vert * in_rect.zw;
    gl_Position = vec4(pos, 0.0, 1.0);
}
"""

WK_FRAGMENT_SHADER = """
#version 330 core
in vec2 uv;
in vec4 v_rect;
in vec4 v_color;
in float v_pressed;
out vec4 fragColor;
uniform vec2 u_resolution;

void main() {
    vec2 size_px = v_rect.zw * 0.5 * u_resolution;
    vec2 px = uv * size_px;
    
    // Base ivory white key
    vec3 col = vec3(0.95, 0.96, 0.98);
    // Vertical shading
    col *= mix(0.92, 1.0, uv.y);
    
    // Side dividers (1px subtle dark border)
    float border_x = min(px.x, size_px.x - px.x);
    if (border_x < 1.0) {
        col *= 0.65;
    }
    // Bottom chamfer
    if (px.y < 5.0) {
        col *= 0.75 + 0.25 * (px.y / 5.0);
    }
    
    // When key is struck:
    if (v_pressed > 0.001) {
        float glow_falloff = pow(uv.y, 1.4);
        vec3 press_col = mix(col * 0.75, v_color.rgb, glow_falloff * 0.90 * v_pressed);
        // Bright hit edge at top
        if (uv.y > 0.96) {
            press_col = mix(press_col, vec3(1.0), 0.55 * v_pressed);
        }
        col = press_col;
    }
    
    fragColor = vec4(col, 1.0);
}
"""

# Black keys instanced shader
BK_VERTEX_SHADER = """
#version 330 core
in vec2 in_vert;
in vec4 in_rect;
in vec4 in_color;
in float in_pressed;

out vec2 uv;
out vec4 v_rect;
out vec4 v_color;
out float v_pressed;

void main() {
    uv = in_vert;
    v_rect = in_rect;
    v_color = in_color;
    v_pressed = in_pressed;
    vec2 pos = in_rect.xy + in_vert * in_rect.zw;
    gl_Position = vec4(pos, 0.0, 1.0);
}
"""

BK_FRAGMENT_SHADER = """
#version 330 core
in vec2 uv;
in vec4 v_rect;
in vec4 v_color;
in float v_pressed;
out vec4 fragColor;
uniform vec2 u_resolution;

void main() {
    vec2 size_px = v_rect.zw * 0.5 * u_resolution;
    vec2 px = uv * size_px;
    
    // Matte dark obsidian
    vec3 col = vec3(0.11, 0.115, 0.135);
    // Subtle top specular highlight
    if (uv.y > 0.95) {
        col += vec3(0.14, 0.14, 0.16);
    }
    // Bottom edge bevel
    if (px.y < 3.5) {
        col *= 0.55;
    }
    // Side bevels
    if (px.x < 2.0 || px.x > size_px.x - 2.0) {
        col *= 0.78;
    }
    
    // Pressed state
    if (v_pressed > 0.001) {
        float glow_falloff = pow(uv.y, 1.1);
        vec3 press_col = mix(v_color.rgb * 0.45, v_color.rgb * 1.15, glow_falloff * v_pressed);
        if (uv.y > 0.95) {
            press_col = mix(press_col, vec3(1.0), 0.65 * v_pressed);
        }
        col = press_col;
    }
    
    // Rounded bottom corners on black keys
    float r = 3.5;
    if (px.y < r) {
        if (px.x < r && length(vec2(px.x - r, px.y - r)) > r) discard;
        if (px.x > size_px.x - r && length(vec2(px.x - (size_px.x - r), px.y - r)) > r) discard;
    }
    
    fragColor = vec4(col, 1.0);
}
"""

# Falling notes instanced shader
NOTES_VERTEX_SHADER = """
#version 330 core
in vec2 in_vert; // (0,0) to (1,1)
in vec4 in_rect; // x, y, w, h in NDC
in vec4 in_color; // r, g, b, a
in vec4 in_params; // corner_radius_px, is_active, velocity_norm, 0

out vec2 uv;
out vec4 v_rect;
out vec4 v_color;
out vec4 v_params;

void main() {
    uv = in_vert;
    v_rect = in_rect;
    v_color = in_color;
    v_params = in_params;
    vec2 pos = in_rect.xy + in_vert * in_rect.zw;
    gl_Position = vec4(pos, 0.0, 1.0);
}
"""

NOTES_FRAGMENT_SHADER = """
#version 330 core
in vec2 uv;
in vec4 v_rect;
in vec4 v_color;
in vec4 v_params;
out vec4 fragColor;
uniform vec2 u_resolution;

void main() {
    vec2 size_px = v_rect.zw * 0.5 * u_resolution;
    vec2 p_px = (uv - 0.5) * size_px;
    float r = min(v_params.x, min(size_px.x, size_px.y) * 0.5);
    
    // SDF rounded rectangle
    vec2 q = abs(p_px) - (size_px * 0.5 - vec2(r));
    float d = length(max(q, 0.0)) + min(max(q.x, q.y), 0.0) - r;
    float alpha = 1.0 - smoothstep(-0.8, 0.8, d);
    if (alpha <= 0.0) discard;
    
    // Gradient: brighter at bottom edge
    float vert_grad = mix(1.28, 0.78, uv.y);
    float horiz_bevel = 1.0 - 0.22 * pow(abs(uv.x - 0.5) * 2.0, 6.0);
    vec3 col = v_color.rgb * vert_grad * horiz_bevel;
    
    // Active pulse if currently striking keyboard
    if (v_params.y > 0.5 && uv.y < 0.12) {
        float flare = 1.0 - uv.y / 0.12;
        col = mix(col, vec3(1.0), flare * 0.70);
    }
    
    fragColor = vec4(col, alpha * v_color.a);
}
"""

# Radial hit flare / bloom shader (Additive blending)
FLARE_VERTEX_SHADER = """
#version 330 core
in vec2 in_vert;
in vec4 in_rect; // center_x, center_y, width, height
in vec4 in_color;

out vec2 uv;
out vec4 v_color;

void main() {
    uv = in_vert;
    v_color = in_color;
    vec2 pos = in_rect.xy + (in_vert - 0.5) * in_rect.zw;
    gl_Position = vec4(pos, 0.0, 1.0);
}
"""

FLARE_FRAGMENT_SHADER = """
#version 330 core
in vec2 uv;
in vec4 v_color;
out vec4 fragColor;

void main() {
    vec2 p = (uv - 0.5) * 2.0;
    // Anisotropic bloom flare: wider horizontally, soft vertically
    float dist = length(p * vec2(1.0, 2.0));
    float glow = exp(-dist * dist * 3.5);
    if (glow < 0.005) discard;
    
    vec3 col = v_color.rgb * glow * 1.4;
    fragColor = vec4(col, glow * 0.85);
}
"""

# Hit sparks / embers shader (Additive blending)
SPARK_VERTEX_SHADER = """
#version 330 core
in vec2 in_vert; // [-1, 1] unit quad
in vec4 in_pos_size; // x, y, size_x, size_y
in vec4 in_color; // r, g, b, alpha

out vec2 uv;
out vec4 v_color;

void main() {
    uv = in_vert;
    v_color = in_color;
    vec2 pos = in_pos_size.xy + in_vert * in_pos_size.zw;
    gl_Position = vec4(pos, 0.0, 1.0);
}
"""

SPARK_FRAGMENT_SHADER = """
#version 330 core
in vec2 uv;
in vec4 v_color;
out vec4 fragColor;

void main() {
    float d = length(uv);
    if (d > 1.0) discard;
    float glow = exp(-d * d * 3.5);
    fragColor = vec4(v_color.rgb * glow * 1.5, glow * v_color.a);
}
"""

# Hit bar separator shader
HITBAR_VERTEX_SHADER = """
#version 330 core
in vec2 in_vert;
out vec2 uv;
uniform float u_hit_y_ndc;
uniform float u_bar_height_ndc;

void main() {
    uv = in_vert;
    // Quad from y_hit_ndc to y_hit_ndc + bar_height_ndc across x in [-1, 1]
    vec2 pos = vec2(in_vert.x * 2.0 - 1.0, u_hit_y_ndc + in_vert.y * u_bar_height_ndc);
    gl_Position = vec4(pos, 0.0, 1.0);
}
"""

HITBAR_FRAGMENT_SHADER = """
#version 330 core
in vec2 uv;
out vec4 fragColor;

void main() {
    // Sleek metallic bezel bar
    vec3 col = vec3(0.12, 0.13, 0.16);
    // Top highlight rim
    if (uv.y > 0.80) {
        col += vec3(0.15, 0.16, 0.20);
    }
    // Bottom shadow
    if (uv.y < 0.20) {
        col *= 0.60;
    }
    fragColor = vec4(col, 1.0);
}
"""
