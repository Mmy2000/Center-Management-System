"""Layer 3: hiding what this center did not buy (docs/10 §N.8, TASK-108).

    {% load features %}

    {% if_feature "payments" %} … {% else %} … {% endif_feature %}
    {% if "payments"|feature_on %} … {% endif %}

The sidebar already gates on ``perms.*``; feature checks sit alongside it, and
the context processor exposes ``FEATURES`` as a frozenset so the common case is
just ``{% if "payments" in FEATURES %}`` with no extra tag at all.

Hiding a link is a courtesy, not a control — the view and the endpoint refuse
independently (layers 1, 2 and 4). Nothing here is load-bearing for security.
"""

from django import template
from django.template.base import NodeList

from apps.tenancy.resolver import has_feature

register = template.Library()


@register.filter(name="feature_on")
def feature_on(feature_key: str) -> bool:
    """``{% if "payments"|feature_on %}`` — an unknown key still raises."""
    return has_feature(feature_key)


class IfFeatureNode(template.Node):
    def __init__(self, feature_key, nodelist_true: NodeList, nodelist_false: NodeList):
        self.feature_key = feature_key
        self.nodelist_true = nodelist_true
        self.nodelist_false = nodelist_false

    def render(self, context):
        key = self.feature_key.resolve(context)
        if has_feature(key):
            return self.nodelist_true.render(context)
        return self.nodelist_false.render(context)


@register.tag("if_feature")
def do_if_feature(parser, token):
    try:
        _tag_name, raw_key = token.split_contents()
    except ValueError:
        raise template.TemplateSyntaxError(
            "if_feature takes exactly one argument: the feature key"
        ) from None

    nodelist_true = parser.parse(("else", "endif_feature"))
    token = parser.next_token()
    if token.contents == "else":
        nodelist_false = parser.parse(("endif_feature",))
        parser.delete_first_token()
    else:
        nodelist_false = NodeList()

    return IfFeatureNode(parser.compile_filter(raw_key), nodelist_true, nodelist_false)
