import itertools as it
import json
import re

from jsonschema.protocols import Validator
from referencing import Registry, Resource
from referencing._core import Resolver
from referencing.jsonschema import DRAFT202012

STRING_INNER = r'(?:[^"\\\x00-\x1f\x7f-\x9f]|\\.)'
STRING = f'"{STRING_INNER}*"'
INTEGER = r"(0|[1-9][0-9]*)"
NUMBER = rf"(-)?({INTEGER})(\.[0-9]+)?([eE][+-][0-9]+)?"
BOOLEAN = r"(true|false)"
NULL = r"null"

type_to_regex = {
    "string": STRING,
    "integer": INTEGER,
    "number": NUMBER,
    "boolean": BOOLEAN,
    "null": NULL,
}


def build_regex_from_schema(schema: str):
    """Turn a JSON schema into a regex that matches any JSON object that follows
    this schema.

    Parameters
    ----------
    schema
        A string that contains the JSON schema.

    Returns
    -------
    A string that contains a regular expression that matches any JSON object that
    follows the schema.

    """
    Validator.check_schema(schema)
    schema = json.loads(schema)

    # Build reference resolver
    schema = Resource(contents=schema, specification=DRAFT202012)
    uri = schema.id() if schema.id() is not None else ""
    registry = Registry().with_resource(uri=uri, resource=schema)
    resolver = registry.resolver()

    content = schema.contents
    return to_regex(resolver, content)


def to_regex(resolver: Resolver, instance: dict):
    """Translate a JSON Schema instance into a regex that validates the schema.

    Note
    ----
    Many features of JSON schema are missing:
    - Support the fact that fields in an object are optional by default
    - Handle `required` keyword
    - Handle `additionalProperties` keyword
    - Handle types defined as a list
    - Handle constraints on numbers
    - Handle special patterns: `date`, `uri`, etc.

    Parameters
    ----------
    resolver
        An object that resolves references to other instances within a schema
    instance
        The instance to translate
    """
    whitespace = r"[\n ]*"

    if "properties" in instance:
        regex = ""
        regex += r"\{"
        for i, (name, value) in enumerate(instance["properties"].items()):
            regex += f'{whitespace}"{name}"{whitespace}:{whitespace}'
            regex += to_regex(resolver, value)

            # No comma after the last key-value pair in JSON
            if i < len(instance["properties"]) - 1:
                regex += f"{whitespace},"

        regex += f"{whitespace}" + r"\}"

        return regex

    # To validate against allOf, the given data must be valid against all of the
    # given subschemas.
    elif "allOf" in instance:
        subregexes = [to_regex(resolver, t) for t in instance["allOf"]]
        subregexes_str = [f"{subregex}" for subregex in subregexes]
        return rf"({''.join(subregexes_str)})"

    # To validate against `anyOf`, the given data must be valid against
    # any (one or more) of the given subschemas.
    elif "anyOf" in instance:
        subregexes = [to_regex(resolver, t) for t in instance["anyOf"]]
        combinations = [
            "(" + "".join(c) + ")"
            for r in range(1, len(subregexes) + 1)
            for c in it.permutations(subregexes, r)
        ]

        return rf"({'|'.join(combinations)})"

    # To validate against oneOf, the given data must be valid against exactly
    # one of the given subschemas.
    elif "oneOf" in instance:
        subregexes = [to_regex(resolver, t) for t in instance["oneOf"]]
        return rf"({'|'.join(subregexes)})"

    # The enum keyword is used to restrict a value to a fixed set of values. It
    # must be an array with at least one element, where each element is unique.
    elif "enum" in instance:
        if instance["type"] == "string":
            choices = [f'"{re.escape(choice)}"' for choice in instance["enum"]]
            return f"({'|'.join(choices)})"
        else:
            choices = [re.escape(str(choice)) for choice in instance["enum"]]
            return f"({'|'.join(choices)})"

    elif "$ref" in instance:
        path = f"{instance['$ref']}"
        instance = resolver.lookup(path).contents
        return to_regex(resolver, instance)

    # The type keyword may either be a string or an array:
    # - If it's a string, it is the name of one of the basic types.
    # - If it is an array, it must be an array of strings, where each string is
    # the name of one of the basic types, and each element is unique. In this
    # case, the JSON snippet is valid if it matches any of the given types.
    elif "type" in instance:
        type = instance["type"]
        if type == "string":
            if "maxLength" in instance or "minLength" in instance:
                max_length = instance.get("maxLength", "")
                min_length = instance.get("minLength", "")
                try:
                    if int(max_length) < int(min_length):
                        raise ValueError(
                            "maxLength must be greater than or equal to minLength"
                        )
                except ValueError:
                    pass
                return f'"{STRING_INNER}{{{min_length},{max_length}}}"'
            elif "pattern" in instance:
                pattern = instance["pattern"]
                if pattern[0] == "^" and pattern[-1] == "$":
                    return rf'(^"{pattern[1:-1]}"$)'
                else:
                    return rf'("{pattern}")'
            else:
                return type_to_regex["string"]

        elif type == "number":
            return type_to_regex["number"]

        elif type == "integer":
            return type_to_regex["integer"]

        elif type == "array":
            if "items" in instance:
                items_regex = to_regex(resolver, instance["items"])
                return rf"\[({items_regex})(,({items_regex}))*\]"
            else:
                # Here we need to make the choice to exclude generating list of objects
                # if the specification of the object is not give, even though a JSON
                # object that contains an object here would be valid under the specification.
                types = [
                    {"type": "boolean"},
                    {"type": "null"},
                    {"type": "number"},
                    {"type": "integer"},
                    {"type": "string"},
                ]
                regexes = [to_regex(resolver, t) for t in types]
                return rf"\[({'|'.join(regexes)})(,({'|'.join(regexes)}))*\]"

        elif type == "boolean":
            return type_to_regex["boolean"]

        elif type == "null":
            return type_to_regex["null"]

        elif isinstance(type, list):
            # Here we need to make the choice to exclude generating an object
            # if the specification of the object is not give, even though a JSON
            # object that contains an object here would be valid under the specification.
            regexes = [to_regex(resolver, {"type": t}) for t in type if t != "object"]
            return rf"({'|'.join(regexes)})"

    raise NotImplementedError(
        f"""Could not translate the instance {instance} to a
    regular expression. Make sure it is valid to the JSON Schema specification. If
    it is, please open an issue on the Outlines repository"""
    )
