from django import template

from audits.roles import user_has_any_role

register = template.Library()


@register.filter
def has_any_role(user, role_names):
    if isinstance(role_names, str):
        roles = [role.strip() for role in role_names.split("|") if role.strip()]
    else:
        roles = role_names or []
    return user_has_any_role(user, roles)
