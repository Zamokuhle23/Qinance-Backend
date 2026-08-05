"""Tool registry for Ask Qinance — Qinance-Backend.

Tools are grouped by role: merchant, customer, admin.
Gemini never queries PostgreSQL directly — registered tools do.
"""

import inspect

_registry = {}


def register_tool(name, roles=None, description=''):
    """Decorator to register a tool with role-based access control."""
    roles = roles or ['merchant']

    def decorator(func):
        sig = inspect.signature(func)
        _registry[name] = {
            'function': func,
            'roles': roles,
            'description': description,
            'parameters': {
                name: {
                    'kind': p.annotation if p.annotation is not inspect.Parameter.empty else 'str',
                    'default': p.default if p.default is not inspect.Parameter.empty else None,
                    'required': p.default is inspect.Parameter.empty,
                }
                for name, p in sig.parameters.items()
            },
        }
        return func

    return decorator


def get_tool(name):
    return _registry.get(name)


def list_tools_for_role(role):
    """List tools (metadata only) available to a given role."""
    role = (role or '').lower()
    result = []
    for name, tool in _registry.items():
        if role in tool['roles'] or 'admin' in tool['roles']:
            result.append({
                'name': name,
                'description': tool['description'],
                'parameters': tool['parameters'],
            })
    return result


def execute_tool(name, role, args=None):
    """Execute a tool with role access control. Runs in Python, never exposes DB to Gemini."""
    tool = _registry.get(name)
    if not tool:
        return {'ok': False, 'error': f'Unknown tool: {name}'}
    if role not in tool['roles'] and 'admin' not in tool['roles']:
        return {'ok': False, 'error': f'Tool {name} not allowed for role {role}'}
    try:
        return tool['function'](**(args or {}))
    except Exception as e:
        return {'ok': False, 'error': f'Tool error: {e}'}