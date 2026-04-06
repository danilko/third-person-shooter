shader_type canvas_item;

uniform float blur_amount : hint_range(0.0, 5.0) = 2.0;
// We add 'repeat_disable' to prevent edge bleeding
uniform sampler2D screen_tex : hint_screen_texture, filter_linear_mipmap, repeat_disable;

void fragment() {
    // We sample the screen texture specifically
    vec4 color = textureLod(screen_tex, SCREEN_UV, blur_amount);
    
    // Force the RGB to show while keeping alpha at 1.0
    COLOR.rgb = color.rgb;
    COLOR.a = 1.0;
}