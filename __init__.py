import bpy
from bpy.props import BoolProperty, PointerProperty, StringProperty
from bpy.types import Panel, PropertyGroup, Menu

_last_signature = ""

def get_action_range(action, use_manual=False):
    # Если галочка Prefer Manual Range ВКЛЮЧЕНА, и в экшене включен мануал
    if use_manual and getattr(action, "use_frame_range", False):
        return int(action.frame_start), int(action.frame_end)
    
    # Во всех остальных случаях берем РЕАЛЬНЫЕ ключи.
    # Используем curve_frame_range, так как frame_range в Blender 5.2
    # возвращает мануальные значения, если они включены.
    try:
        start = int(action.curve_frame_range[0])
        end = int(action.curve_frame_range[1])
    except Exception:
        # Фоллбэк на всякий случай
        try:
            start = int(action.frame_range[0])
            end = int(action.frame_range[1])
        except Exception:
            return 1, 250
            
    return (start, end) if end > start else (1, 250)

def force_update_range(self, context):
    global _last_signature
    _last_signature = ""  # Сбрасываем кэш
    
    scene = context.scene
    props = scene.animqol_props
    obj = props.target_object
    
    # 1. Если выключили автофит -> ставим дефолт и уходим
    if not props.auto_fit_range:
        scene.frame_start = 1
        scene.frame_end = 250
        scene.frame_set(1)
        return

    # 2. Если автофит включен, но объекта/экшена нет - ничего не делаем
    if not (obj and obj.animation_data and obj.animation_data.action):
        return

    action = obj.animation_data.action
    
    # 3. Считаем диапазон
    start, end = get_action_range(action, props.use_manual_range)
    
    # 4. Применяем
    if scene.frame_start != start or scene.frame_end != end:
        scene.frame_start = start
        scene.frame_end = end
        scene.frame_set(scene.frame_current)

class AnimPlayProps(PropertyGroup):
    target_object: PointerProperty(
        name="Target Object",
        type=bpy.types.Object,
        poll=lambda self, obj: obj.type == 'ARMATURE' and obj.animation_data is not None,
        description="Select the armature to view animations"
    )
    loop_enabled: BoolProperty(
        name="Loop Animation", 
        default=True,
        description="Loop the playback. If disabled, stops at the end and resets"
    )
    
    auto_fit_range: BoolProperty(
        name="Auto-fit Range", 
        default=True,
        update=force_update_range,
        description="Automatically adjust the scene timeline to match the action length"
    )
    
    use_manual_range: BoolProperty(
        name="Prefer Manual Range", 
        default=True,
        update=force_update_range,
        description="Use the action's Manual Frame Range if it is enabled"
    )

class ANIMQOL_OT_set_action(bpy.types.Operator):
    bl_idname = "animqol.set_action"
    bl_label = "Set Action"
    bl_description = "Assign the selected action to the armature"
    
    action_name: StringProperty()

    def execute(self, context):
        props = context.scene.animqol_props
        obj = props.target_object
        if obj and obj.animation_data:
            act = bpy.data.actions.get(self.action_name)
            if act:
                obj.animation_data.action = act
        return {'FINISHED'}

class ANIMQOL_OT_unlink_action(bpy.types.Operator):
    bl_idname = "animqol.unlink_action"
    bl_label = "Unlink Action"
    bl_description = "Unlink the active action from the armature"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        props = context.scene.animqol_props
        obj = props.target_object
        if obj and obj.animation_data:
            obj.animation_data.action = None
        return {'FINISHED'}

class ANIMQOL_MT_action_menu(Menu):
    bl_label = "Action Menu"
    bl_idname = "ANIMQOL_MT_action_menu"
    bl_description = "Click to choose an action from all animations in the file"

    def draw(self, context):
        layout = self.layout
        for action in bpy.data.actions:
            op = layout.operator("animqol.set_action", text=action.name)
            op.action_name = action.name

def update_frame_range_from_action(scene, depsgraph):
    global _last_signature
    props = scene.animqol_props

    obj = props.target_object
    if not (obj and obj.animation_data):
        return
        
    action = obj.animation_data.action
    if not action:
        return

    if not props.auto_fit_range:
        return

    current_sig = f"{action.name}_{getattr(action, 'use_frame_range', False)}_{action.frame_start}_{action.frame_end}_{props.use_manual_range}"
    
    if current_sig != _last_signature:
        _last_signature = current_sig
        start, end = get_action_range(action, props.use_manual_range)
        
        if scene.frame_start != start or scene.frame_end != end:
            scene.frame_start = start
            scene.frame_end = end
            scene.frame_set(scene.frame_current)

def check_playback_loop(scene, depsgraph):
    props = scene.animqol_props
    if bpy.context.screen.is_animation_playing and not props.loop_enabled:
        if scene.frame_current >= scene.frame_end:
            bpy.ops.screen.animation_cancel()
            scene.frame_set(scene.frame_start)

class ANIMQOL_PT_main_panel(Panel):
    bl_label = "Anim Play QOL"
    bl_idname = "ANIMQOL_PT_main_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Marionette"
    bl_order = 30

    def draw(self, context):
        layout = self.layout
        props = context.scene.animqol_props

        layout.prop(props, "target_object", text="")
        
        obj = props.target_object
        if obj and obj.animation_data:
            layout.separator()
            
            act = obj.animation_data.action
            
            row = layout.row(align=True)
            menu_text = act.name if act else "Select Action"
            row.menu("ANIMQOL_MT_action_menu", text=menu_text, icon='ACTION')
            
            if act:
                row.operator("animqol.unlink_action", text="", icon='X')
            
            layout.separator()
            col = layout.column(align=True)
            
            row = col.row(align=True)
            row.scale_y = 1.5
            row.operator("screen.frame_jump", text="", icon='REW').end = False
            if bpy.context.screen.is_animation_playing:
                row.operator("screen.animation_play", text="Pause", icon='PAUSE')
            else:
                row.operator("screen.animation_play", text="Play", icon='PLAY')
            row.operator("screen.frame_jump", text="", icon='FF').end = True

            col.separator()
            col.prop(props, "loop_enabled", text="Loop")
            col.prop(props, "auto_fit_range", text="Auto-fit Range")
            
            subcol = col.column(align=True)
            subcol.enabled = props.auto_fit_range
            subcol.prop(props, "use_manual_range", text="Prefer Manual Range")
            
            if props.auto_fit_range and act:
                start, end = get_action_range(act, props.use_manual_range)
                is_manual_used = props.use_manual_range and getattr(act, "use_frame_range", False)
                range_text = f"Range: {start} — {end}"
                if is_manual_used:
                    range_text += " (Manual Range)"
                
                row = col.row(align=True)
                row.label(text=range_text, icon='PREVIEW_RANGE')

classes = (
    AnimPlayProps,
    ANIMQOL_OT_set_action,
    ANIMQOL_OT_unlink_action,
    ANIMQOL_MT_action_menu,
    ANIMQOL_PT_main_panel,
)

def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.animqol_props = PointerProperty(type=AnimPlayProps)
    bpy.app.handlers.depsgraph_update_post.append(update_frame_range_from_action)
    bpy.app.handlers.frame_change_post.append(check_playback_loop)

def unregister():
    if update_frame_range_from_action in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.remove(update_frame_range_from_action)
    if check_playback_loop in bpy.app.handlers.frame_change_post:
        bpy.app.handlers.frame_change_post.remove(check_playback_loop)
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
    del bpy.types.Scene.animqol_props

if __name__ == "__main__":
    register()