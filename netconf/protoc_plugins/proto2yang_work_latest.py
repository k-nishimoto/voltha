#!/usr/bin/env python
#
# Copyright 2016 the original author or authors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#

"""protoc plugin to convert a protobuf schema to a yang schema

   - basic support for message, fields. enumeration, service, method

   - yang semantic rules needs to be implemented

   - to run this plugin :

   $ python -m grpc.tools.protoc -I.
   --plugin=protoc-gen-custom=./proto2yang.py --custom_out=. <proto file>.proto

   - the above will produce a ietf-<proto file>.yang file formatted for yang

   - two examples of proto that can be used in the same directory are
   yang.proto and addressbook.proto

"""

import sys

from jinja2 import Template
from google.protobuf.compiler import plugin_pb2 as plugin
from descriptor_parser import DescriptorParser

from google.protobuf.descriptor import FieldDescriptor

template_yang = Template("""
module ietf-{{ module.name }} {

    {% macro set_module_prefix(type) %}
        {% for t in module.data_types %}
            {% if t.type == type %}
                {% if t.module != module.name %} {{ t.module }}:{{ type }};
                {% else %} {{ type }};
                {% endif %}
                {% set found=True %}
            {% endif %}
        {% if loop.last %}
            {% if not found %} {{ type }}; {% endif %}
        {% endif %}
        {% endfor %}
    {% endmacro %}

    namespace "urn:opencord:params:xml:ns:voltha:ietf-{{ module.name }}";
    prefix {{ module.name }};

    {% for imp in module.imports %}
    import ietf-{{ imp.name }} { prefix {{ imp.name }} ; }
    {% endfor %}

    organization "CORD";
    contact
        " Any name";

    description
        "{{ module.description }}";

    revision "2016-11-15" {
        description "Initial revision.";
        reference "reference";
    }

    {% for enum in module.enums %}
    typedef {{ enum.name }} {
        type enumeration {
        {% for v in enum.value %}
            enum {{ v.name }} {
                description "{{ v.description }}";
            }
        {% endfor %}
        }
        description
            "{{ enum.description }}";
    }
    {% endfor %}

    {% for message in module.messages recursive %}
    {% if message.name in module.referred_messages %}
    grouping {{ message.name }} {
    {% else %}
    container {{ message.name }} {
    {% endif %}
        description
            "{{ message.description }}";
        {% for field in message.fields %}
        {% if field.type_ref %}
        {% for dict_item in module.referred_messages_with_keys %}
            {% if dict_item.name == field.type %}
                {% if not field.repeated %}
        container {{ field.name }} {
                {% else %}
        list {{ field.name }} {
            key "{{ dict_item.key }}";
            {% if not field.repeated %}
            max-elements 1;
            {% endif %}
            {% endif %}
            uses {{ set_module_prefix(field.type) }}
            description
                "{{ field.description }}";
        }
            {% endif %}
        {% endfor %}
        {% elif field.repeated %}
        list {{ field.name }} {
            key "{{ field.name }}";
            leaf {{ field.name }} {
                {% if field.type == "decimal64" %}
                type {{ field.type }} {
                   fraction-digits 5;
                }
                {% else %}
                type {{ set_module_prefix(field.type) }}
                {% endif %}
                description
                    "{{ field.description }}";
            }
            description
                "{{ field.description }}";
        }
        {% else %}
        leaf {{ field.name }} {
            {% if field.type == "decimal64" %}
            type {{ field.type }} {
               fraction-digits 5;
            }
            {% else %}
            type {{ set_module_prefix(field.type) }}
            {% endif %}
            description
                "{{ field.description }}";
        }
        {% endif %}

        {% endfor %}
        {% for enum_type in message.enums %}
        typedef {{ enum_type.name }} {
            type enumeration {
            {% for v in enum_type.value %}
                enum {{ v.name }} {
                    description "{{ v.description }}";
                }
            {% endfor %}
            }
            description
                "{{ enum_type.description }}";
        }

        {% endfor %}
    {% if message.messages %}
    {{ loop (message.messages)|indent(4, false) }}
    {% endif %}
    }

    {% endfor %}
    {% for service in module.services %}
    {% if service.description %}
    /*  {{ service.description }}" */
    {% endif %}
    {% for method in service.methods %}
    rpc {{ service.service }}-{{ method.method }} {
        description
            "{{ method.description }}";
        {% if method.input %}
        input {
            {% if method.input_ref %}
            uses {{ set_module_prefix(method.input) }}
            {% else %}
            leaf {{ method.input }} {
                type {{ set_module_prefix(method.input) }}
            }
            {% endif %}
        }
        {% endif %}
        {% if method.output %}
        output {
            {% if method.output_ref %}
            uses {{ set_module_prefix(method.output) }}
            {% else %}
            leaf {{ method.output }} {
                type {{ set_module_prefix(method.output) }}
            }
            {% endif %}
        }
        {% endif %}
    }

    {% endfor %}

    {% endfor %}
}
""", trim_blocks=True, lstrip_blocks=True)

def traverse_dependencies(descriptor):
    dependencies = []
    proto_imports = descriptor.get('dependency', [])
    for proto_import in proto_imports:
        # If the import file has a directory path to it remove it as it is not
        # allowed in Yang.  The proto extension should be removed as well
        dependencies.append (
            {
            'name' : proto_import.split('/')[-1][:-len('.proto')]
            }
        )
    return dependencies


def traverse_messages(message_types, prefix, referenced_messages):
    messages = []
    for message_type in message_types:
        assert message_type['_type'] == 'google.protobuf.DescriptorProto'

        # full_name = prefix + '-' + message_type['name']
        full_name = message_type['name']

        # parse the fields
        fields = traverse_fields(message_type.get('field', []), full_name,
                                 referenced_messages)

        # parse the enums
        enums = traverse_enums(message_type.get('enum_type', []), full_name)

        # parse nested messages
        nested = message_type.get('nested_type', [])
        nested_messages = traverse_messages(nested, full_name,
                                            referenced_messages)
        messages.append(
            {
                'name': full_name,
                'fields': fields,
                'enums': enums,
                # 'extensions': extensions,
                'messages': nested_messages,
                'description': remove_unsupported_characters(
                    message_type.get('_description', '')),
                # 'extension_ranges': extension_ranges,
                # 'oneof': oneof
            }
        )
    return messages


def traverse_fields(fields_desc, prefix, referenced_messages):
    fields = []
    for field in fields_desc:
        assert field['_type'] == 'google.protobuf.FieldDescriptorProto'
        yang_base_type = is_base_type(field['type'])
        _type = get_yang_type(field)
        if not yang_base_type:
            referenced_messages.append(_type)

        fields.append(
            {
                # 'name': prefix + '-' + field.get('name', ''),
                'name': field.get('name', ''),
                'label': field.get('label', ''),
                'repeated': field['label'] == FieldDescriptor.LABEL_REPEATED,
                'number': field.get('number', ''),
                'options': field.get('options', ''),
                'type_name': field.get('type_name', ''),
                'type': _type,
                'type_ref': not yang_base_type,
                'description': remove_unsupported_characters(field.get(
                    '_description', ''))
            }
        )
    return fields


def traverse_enums(enums_desc, prefix):
    enums = []
    for enum in enums_desc:
        assert enum['_type'] == 'google.protobuf.EnumDescriptorProto'
        # full_name = prefix + '-' + enum.get('name', '')
        full_name = enum.get('name', '')
        enums.append(
            {
                'name': full_name,
                'value': enum.get('value', ''),
                'description': remove_unsupported_characters(enum.get(
                    '_description', ''))
            }
        )
    return enums


def traverse_services(service_desc, referenced_messages):
    services = []
    for service in service_desc:
        methods = []
        for method in service.get('method', []):
            assert method['_type'] == 'google.protobuf.MethodDescriptorProto'

            input_name = method.get('input_type')
            input_ref = False
            if not is_base_type(input_name):
                input_name = remove_first_character_if_match(input_name, '.')
                # input_name = input_name.replace(".", "-")
                input_name = input_name.split('.')[-1]
                referenced_messages.append(input_name)
                input_ref = True

            output_name = method.get('output_type')
            output_ref = False
            if not is_base_type(output_name):
                output_name = remove_first_character_if_match(output_name, '.')
                # output_name = output_name.replace(".", "-")
                output_name = output_name.split('.')[-1]
                referenced_messages.append(output_name)
                output_ref = True

            methods.append(
                {
                    'method': method.get('name', ''),
                    'input': input_name,
                    'input_ref': input_ref,
                    'output': output_name,
                    'output_ref': output_ref,
                    'description': remove_unsupported_characters(method.get(
                        '_description', '')),
                    'server_streaming': method.get('server_streaming',
                                                   False) == True
                }
            )
        services.append(
            {
                'service': service.get('name', ''),
                'methods': methods,
                'description': remove_unsupported_characters(service.get(
                    '_description', '')),
            }
        )
    return services


def rchop(thestring, ending):
    if thestring.endswith(ending):
        return thestring[:-len(ending)]
    return thestring


def traverse_desc(descriptor):
    referenced_messages = []
    name = rchop(descriptor.get('name', ''), '.proto')
    package = descriptor.get('package', '')
    description = descriptor.get('_description', '')
    # imports=traverse_dependencies(descriptor)
    messages = traverse_messages(descriptor.get('message_type', []),
                                 package, referenced_messages)
    enums = traverse_enums(descriptor.get('enum_type', []), package)
    services = traverse_services(descriptor.get('service', []),
                                 referenced_messages)
    # extensions = _traverse_extensions(descriptors)
    # options = _traverse_options(descriptors)
    # set_messages_keys(messages)
    # unique_referred_messages_with_keys = []
    # for message_name in list(set(referenced_messages)):
    #     unique_referred_messages_with_keys.append(
    #         {
    #             'name': message_name,
    #             'key': get_message_key(message_name, messages)
    #         }
    #     )

    # Get a list of type definitions (messages, enums) defined in this
    # descriptor
    defined_types = [m['name'].split('/')[-1] for m in messages] + \
                    [e['name'].split('/')[-1]  for e in enums]

    data = {
        'name': name.split('/')[-1],
        'package': package,
        'description': description,
        # 'imports' : imports,
        'messages': messages,
        'enums': enums,
        'services': services,
        'defined_types' : defined_types,
        'referenced_messages': list(set(referenced_messages)),
        # TODO:  simplify for easier jinja2 template use
        # 'referred_messages_with_keys': unique_referred_messages_with_keys,
        # 'extensions': extensions,
        # 'options': options
    }
    return data


def set_messages_keys(messages):
    for message in messages:
        message['key'] = _get_message_key(message, messages)
        if message['messages']:
            set_messages_keys(message['messages'])

def _get_message_key(message, messages):
    # assume key is first yang base type field
    for field in message['fields']:
        if not field['type_ref']:
            return field['name']
        else:
            # if the field name is a message then loop for the key in that
            # message
            ref_message = _get_message(field['type'], messages)
            if ref_message:
                return _get_message_key(ref_message, messages)

    # no key yet - search nested messaged
    for m in message['messages']:
        key = _get_message_key(m, messages)
        if key is not None:
            return key
    else:
        return None

def _get_message(name, messages):
    for m in messages:
        if m['name'] == name:
            return m
    return None

def get_message_key(message_name, messages):
    for message in messages:
        if message_name == message['name']:
            return message['key']
        if message['messages']:
            return get_message_key(message_name, message['messages'])
    return None


def update_module_imports(module):
    used_imports = []
    for ref_msg in module['referenced_messages']:
        for type_dict in module['data_types']:
            if ref_msg == type_dict['type']:
                if module['name'] != type_dict['module']:
                    print '{}++++{}'.format(module['name'], type_dict[
                        'module'])
                    used_imports.append(
                        {
                            'name' : type_dict['module']
                        }
                    )
                break
    module['imports'] = used_imports

def generate_code(request, response):
    assert isinstance(request, plugin.CodeGeneratorRequest)

    parser = DescriptorParser()

    # First process the proto file with the imports
    all_defined_types = []
    all_proto_data = []
    all_referred_messages = []
    all_messages = []
    for proto_file in request.proto_file:
        native_data = parser.parse_file_descriptor(proto_file,
                                                   type_tag_name='_type',
                                                   fold_comments=True)

        # Consolidate the defined types across imports
        yang_data = traverse_desc(native_data)
        for type in yang_data['defined_types']:
            all_defined_types.append(
                {
                    'type' : type,
                    'module' : yang_data['name']
                }
            )

        all_proto_data.append(
            {
                'file_name': '{}-{}'.format('ietf', proto_file.name.split(
                    '/')[-1].replace('.proto','.yang')),
                'module': yang_data
            }
        )

        # Consolidate referred messages across imports
        all_referred_messages = all_referred_messages + yang_data['referenced_messages']

        # consolidate all messages
        all_messages = all_messages + yang_data['messages']

    # Set the message keys - required for List definitions (repeated label)
    set_messages_keys(all_messages)
    unique_referred_messages_with_keys = []
    for m in all_messages:
        unique_referred_messages_with_keys.append(
                {
                    'name': m['name'],
                    'key': m['key']
                }
            )

    # Create the files
    for proto_data in all_proto_data:
        f = response.file.add()
        f.name = proto_data['file_name']
        proto_data['module']['data_types'] = all_defined_types
        proto_data['module']['referred_messages'] = all_referred_messages
        proto_data['module']['referred_messages_with_keys'] = unique_referred_messages_with_keys
        update_module_imports(proto_data['module'])
        f.content = template_yang.render(module=proto_data['module'])


def get_yang_type(field):
    type = field['type']
    if type in YANG_TYPE_MAP.keys():
        _type, _ = YANG_TYPE_MAP[type]
        if _type in ['enumeration', 'message', 'group']:
            return field['type_name'].split('.')[-1]
            # return remove_first_character_if_match(field['type_name'],
            #                                        '.').replace('.', '-')
        else:
            return _type
    else:
        return type


def is_base_type(type):
    # check numeric value of the type first
    if type in YANG_TYPE_MAP.keys():
        _type, _ = YANG_TYPE_MAP[type]
        return _type not in ['message', 'group']
    else:
        # proto name of the type
        result = [_format for (_, _format) in YANG_TYPE_MAP.values() if
                  _format == type and _format not in ['message', 'group']]
        return len(result) > 0


def remove_unsupported_characters(text):
    unsupported_characters = ["{", "}", "[", "]", "\"", "\\", "*", "/"]
    return ''.join([i if i not in unsupported_characters else ' ' for i in
                    text])


def remove_first_character_if_match(str, char):
    if str.startswith(char):
        return str[1:]
    return str


YANG_TYPE_MAP = {
    FieldDescriptor.TYPE_BOOL: ('boolean', 'boolean'),
    FieldDescriptor.TYPE_BYTES: ('binary', 'byte'),
    FieldDescriptor.TYPE_DOUBLE: ('decimal64', 'double'),
    FieldDescriptor.TYPE_ENUM: ('enumeration', 'enum'),
    FieldDescriptor.TYPE_FIXED32: ('int32', 'int64'),
    FieldDescriptor.TYPE_FIXED64: ('int64', 'uint64'),
    FieldDescriptor.TYPE_FLOAT: ('decimal64', 'float'),
    FieldDescriptor.TYPE_INT32: ('int32', 'int32'),
    FieldDescriptor.TYPE_INT64: ('int64', 'int64'),
    FieldDescriptor.TYPE_SFIXED32: ('int32', 'int32'),
    FieldDescriptor.TYPE_SFIXED64: ('int64', 'int64'),
    FieldDescriptor.TYPE_STRING: ('string', 'string'),
    FieldDescriptor.TYPE_SINT32: ('int32', 'int32'),
    FieldDescriptor.TYPE_SINT64: ('int64', 'int64'),
    FieldDescriptor.TYPE_UINT32: ('uint32', 'int64'),
    FieldDescriptor.TYPE_UINT64: ('uint64', 'uint64'),
    FieldDescriptor.TYPE_MESSAGE: ('message', 'message'),
    FieldDescriptor.TYPE_GROUP: ('group', 'group')
}

if __name__ == '__main__':
    # Read request message from stdin
    data = sys.stdin.read()

    # Parse request
    request = plugin.CodeGeneratorRequest()
    request.ParseFromString(data)

    # Create response
    response = plugin.CodeGeneratorResponse()

    # Generate code
    generate_code(request, response)

    # Serialise response message
    output = response.SerializeToString()

    # Write to stdout
    sys.stdout.write(output)
    # print is_base_type(9)
