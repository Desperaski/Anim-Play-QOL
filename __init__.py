import bpy
from bpy.props import BoolProperty, PointerProperty, StringProperty, CollectionProperty, IntProperty
from bpy.types import Panel, PropertyGroup, UIList

_last_signature = ""

def get_action_range(action, use_manual=False):
    if use_manual and getattr(action, "use_frame_range", False):
        return int(action.frame_start), int(action.frame_end)
    
    try:
        start = int(action.curve_frame_range[0])
        end = int(action.curve_frame_range[1])
    except Exception:
        try:
            start = int(action.frame_range[0])
            end = int(action.frame_range[1])
        except Exception:
            return 1, 250
            
    return (start, end) if end > start else (1, 250)

def force_update_range(self, context):
    global _last_signature
    _last_signature = ""
    
    scene = context.scene
    props = scene.animqol_props
    obj = props.target_object
    
    if not props.auto_fit_range:
        scene.frame_start = 1
        scene.frame_end = 250
        scene.frame_set(1)
        return

    if not (obj and obj.animation_data and obj.animation_data.action):
        return

    action = obj.animation_data.action
    start, end = get_action_range(action, props.use_manual_range)
    
    if scene.frame_start != start or scene.frame_end != end:
        scene.frame_start = start
        scene.frame_end = end
        scene.frame_set(scene.frame_current)

class AnimPlayActionItem(PropertyGroup):
    name: StringProperty()

# --- Обновление при клике на элемент списка ---
def update_selected_action(self, context):
    props = context.scene.animqol_props
    obj = props.target_object
    
    if obj and obj.animation_data and props.action_list_index >= 0:
        if props.action_list_index < len(props.action_list):
            action_name = props.action_list[props.action_list_index].name
            act = bpy.data.actions.get(action_name)
            if act:
                obj.animation_data.action = act
                # Закрываем окно (если оно открыто)
                for window in context.window_manager.windows:
                    for area in window.screen.areas:
                        if area.type == 'VIEW_3D':
                            area.tag_redraw()

# --- UIList ---
class ANIMQOL_UL_action_list(UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        layout.label(text=item.name, icon='ACTION')

# --- Свойства ---
class AnimPlayProps(PropertyGroup):
    target_object: PointerProperty(
        name="Target Object",
        type=bpy.types.Object,
        poll=lambda self, obj: obj.type == 'ARMATURE' and obj.animation_data is not None,
        description="Select the armature to view animations"
    )
    loop_enabled: BoolProperty(name="Loop Animation", default=True)
    auto_fit_range: BoolProperty(name="Auto-fit Range", default=True, update=force_update_range)
    use_manual_range: BoolProperty(name="Prefer Manual Range", default=True, update=force_update_range)
    
    search_text: StringProperty(name="Search", default="")
    action_list: CollectionProperty(type=AnimPlayActionItem)
    # Добавляем update-колбэк для мгновенного применения
    action_list_index: IntProperty(default=0, update=update_selected_action)

# --- Оператор обновления списка ---
class ANIMQOL_OT_refresh_list(bpy.types.Operator):
    bl_idname = "animqol.refresh_list"
    bl_label = "Refresh List"

    def execute(self, context):
        props = context.scene.animqol_props
        props.action_list.clear()
        search_lower = props.search_text.lower()
        
        for action in bpy.data.actions:
            if search_lower in action.name.lower():
                item = props.action_list.add()
                item.name = action.name
                
        # Восстанавливаем фокус на текущем экшене, если он в списке
        obj = props.target_object
        if obj and obj.animation_data and obj.animation_data.action:
            current_name = obj.animation_data.action.name
            for i, item in enumerate(props.action_list):
                if item.name == current_name:
                    props.action_list_index = i
                    break
                    
        return {'FINISHED'}

# --- Оператор отвязки ---
class ANIMQOL_OT_unlink_action(bpy.types.Operator):
    bl_idname = "animqol.unlink_action"
    bl_label = "Unlink Action"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        props = context.scene.animqol_props
        obj = props.target_object
        if obj and obj.animation_data:
            obj.animation_data.action = None
        return {'FINISHED'}

# --- Окно поиска ---
class ANIMQOL_OT_search_window(bpy.types.Operator):
    bl_idname = "animqol.search_window"
    bl_label = "Search Action"

    def invoke(self, context, event):
        props = context.scene.animqol_props
        
        # 1. Принудительно собираем список при открытии
        props.action_list.clear()
        for action in bpy.data.actions:
            item = props.action_list.add()
            item.name = action.name
            
        # 2. Ищем текущий экшен и ставим на него фокус
        obj = props.target_object
        if obj and obj.animation_data and obj.animation_data.action:
            current_name = obj.animation_data.action.name
            for i, item in enumerate(props.action_list):
                if item.name == current_name:
                    props.action_list_index = i
                    break

        return context.window_manager.invoke_props_dialog(self, width=400)

    def draw(self, context):
        layout = self.layout
        props = context.scene.animqol_props
        
        row = layout.row(align=True)
        row.prop(props, "search_text", text="")
        row.operator("animqol.refresh_list", text="", icon='FILE_REFRESH')
        
        layout.separator()
        
        # Отрисовка списка. Клик по нему меняет action_list_index, 
        # что вызывает update_selected_action и применяет экшен.
        layout.template_list("ANIMQOL_UL_action_list", "", props, "action_list", props, "action_list_index", rows=10)

    def execute(self, context):
        return {'FINISHED'}

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
            menu_text = act.name if act else "Search Action..."
            
            row.operator("animqol.search_window", text=menu_text, icon='VIEWZOOM')
            
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
    AnimPlayActionItem,
    ANIMQOL_UL_action_list,
    AnimPlayProps,
    ANIMQOL_OT_refresh_list,
    ANIMQOL_OT_unlink_action,
    ANIMQOL_OT_search_window,
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