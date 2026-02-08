# Lines: ~20-25 (validate_citygml_file or similar)

if not os.path.exists(filepath):
    self.report({'ERROR'}, f"File not found: {filepath}")
    return {'CANCELLED'}
if not filepath.lower().endswith('.gml'):
    self.report({'ERROR'}, "Invalid file format. Expected .gml")
    return {'CANCELLED'}
