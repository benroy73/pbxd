from . import v2
from flask import request, abort
from ..app import logger
import xmltodict
from collections import OrderedDict
from ..app import pbx
from flask import current_app as app


def _convert_v3_response_to_v2(pbx_name, termtype, command, v3_response):
    """
    Convert the v3 response to the legacy v2 xml format.
    """
    logger.debug(v3_response)
    obj = {
        'command': {'@cmd': command, '@cmdType': termtype, '@pbxName': pbx_name}
    }
    if v3_response.get('error') is not None:
        obj['command']['error'] = 'ERROR: {}'.format(v3_response['error'])

    elif v3_response.get('screens') is not None:
        screens = []
        for i, screen in enumerate(v3_response['screens']):
            screens.append(OrderedDict([('@page', i + 1), ('#text', screen)]))
        obj['command']['screen'] = screens

    elif v3_response.get('ossi_objects') is not None:
        ossi_objects = []
        for i, o in enumerate(v3_response['ossi_objects']):
            fields = []
            for field in o:
                fields.append(OrderedDict([('@fid', field), ('#text', o[field])]))
            od = OrderedDict([('@i', i + 1), ('field', fields)])
            ossi_objects.append(od)
        if len(ossi_objects) == 0:
            ossi_objects = {}
        obj['command']['ossi_object'] = ossi_objects

    logger.debug(obj)

    xml = xmltodict.unparse(obj, pretty=True, indent='  ')
    return xml


@v2.route("/", methods=["POST"])
def legacy_xml_post():
    """
    pbxName: selects the PBX
    cmdType: must be vt220 or ossi
    fields: may be added with the ossi cmdType to specify which fields to
            retrieve or change with an OSSI command. The field text data is
            only required to change a field on a change command.
    """

    try:  # to parse the v2 command xml
        logger.debug(request.form['request'])
        dom = xmltodict.parse(request.form['request'])
        pbx_name = dom['command']['@pbxName']  # pbx name
        termtype = dom['command']['@cmdType']  # vt220
        command = dom['command']['@cmd']  # vt220
        fields = {}

        if dom['command'].get('field') is not None:
            if not isinstance(dom['command']['field'], list):
                id = dom['command']['field']['@fid']
                fields[id] = dom['command']['field'].get('#text', ' ')
            else:
                for f in dom['command'].get('field', {}):
                    id = f['@fid']
                    fields[id] = f.get('#text', ' ')
    except Exception:
        abort(400, description="Bad request")

    v3_response = pbx.send_pbx_command(termtype, command, fields, debug=False)
    xml = _convert_v3_response_to_v2(pbx_name, termtype, command, v3_response)
    resp = app.make_response(xml)
    resp.mimetype = "text/xml"
    return resp
