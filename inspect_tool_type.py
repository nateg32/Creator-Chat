from google.generativeai import protos
tool = protos.Tool()
print(f"Fields: {tool._meta.fields.keys()}")
try:
    print(f"google_search field type: {type(tool.google_search)}")
except Exception as e:
    print(f"Error accessing google_search: {e}")
