from StringIO import StringIO
from binascii import Error as BaseEncodingError
from collections import defaultdict
from datetime import datetime
from imsvdex.vdex import VDEXError, VDEXManager
from paste.httpserver import serve
from pickle import dump, load
from pyramid.config import Configurator
from pyramid.httpexceptions import HTTPFound, HTTPInternalServerError
from pyramid.response import Response
from pyramid.url import resource_url
from pyramid.view import view_config
from threading import Lock
import base64
import csv
import json
import os
import time
import xlrd
import xlwt

SAMPLE_CSV = '''Level 1,Level 2,Caption en,Description en
key1,,short_desc1,description key1
,key1.1,short_desc1.1,description key1.1
key2,,short_desc2,description key2'''


global_lock = Lock()
here = os.path.dirname(os.path.abspath(__file__))
global_store_name = os.path.join(here, 'store.pickle')


def get_global_store():
    global_lock.acquire(True)
    try:
        global_store = load(file(global_store_name))
    except:
        dump(tuple(), file(global_store_name, 'w'))
        global_store = load(file(global_store_name))
    finally:
        global_lock.release()
    return defaultdict(lambda: VDEX(StringIO(SAMPLE_CSV)),
                       global_store)


def update_global_store(global_store):
    try:
        global_lock.acquire(True)
        dump(global_store.items(), file(global_store_name, 'w'))
    finally:
        global_lock.release()


class VDEX(object):
    """ Represents a persistent vdex vocabulary """
    data = ''

    def __init__(self, data=None):
        if data:
            self.import_vdex(data)

    def import_vdex(self, data):
        """
        Update the internal vdex data based on whatever is passed in as data
        data accepts everything that denso.past_defects.utils.manager_from_file
        accepts
        """
        manager = manager_from_file(data)
        self.data = manager.serialize()
        if hasattr(self, '_v_manager'):
            del self._v_manager

    def export_as_xml(self):
        """ serialize your vdex data to a vdex xml """
        return self.data

    def export_as_csv(self):
        """ serialize your vdex data to csv """
        return manager_to_csv(self._get_manager())

    def export_as_excel(self):
        """ serialize your data to excel """
        return manager_to_excel(self._get_manager())

    def get_matrix(self):
        """ Return a matrix representation of the vdex object """
        manager = self._get_manager()
        return manager.exportMatrix()

    def vocab(self, lang=None):
        """
        Return the vdex as a vocabulary dictionary
        as structured by imsvdex.vdex.VDEXManager.getVocabularyDict
        """
        manager = self._get_manager()
        return manager.getVocabularyDict(lang)

    def get_captions_for_keys(self, keys):
        """
        Return the captions for the given keys
        """
        manager = self._get_manager()
        for key in keys:
            yield manager.getTermCaption(manager.getTermById(key))

    def get_mapping_to_root_keys(self):
        """
            Return a mapping that can be used to find the root key for
            each key
        """
        if not hasattr(self, '_v_mapping_to_root_keys'):
            self._v_mapping_to_root_keys = {}
            for group, (dummy, products) in self.vocab().items():
                for product in products and products.keys() or []:
                    self._v_mapping_to_root_keys[product] = group
                self._v_mapping_to_root_keys[group] = group
        return self._v_mapping_to_root_keys

    def _get_manager(self):
        """ Return the vdex manager. Result is cached! """
        if not hasattr(self, '_v_manager'):
            try:
                self._v_manager = VDEXManager(self.data)
            except VDEXError:
                class FakeManager(object):
                    """ We want to return a manager even if no vdex exists """
                    #pylint:disable=C0103,E0211,R0201
                    def getVocabularyDict(*args):
                        """ return a faked empty dictionary """
                        return {}

                    def exportMatrix(self):
                        """ return a faked empty matrix """
                        return []
                return FakeManager()
        return self._v_manager


def vdex_or_model_to_dynatree(vdex=None, model=None):
    """
        Convert a vdex manager object or a denso vdex model object
        to something understandable by a dynatree widget
    """
    assert not (vdex and model), "Dont give me a vdex object and a model"
    assert (vdex or model), "Dont give me nothing"

    assert isinstance(vdex, VDEXManager) or \
        callable(getattr(model, 'vocab', None)), \
        "The object you gave me is not of the correct type. You "
    "passed it a named argument?"

    retval = []

    def convert(key, value):
        """ converter """
        retval = {}
        retval['title'] = value[0]
        retval['key'] = key
        if value[1]:
            children_keys = value[1].keys()
            children_keys.sort()
            retval['children'] = [convert(x, value[1][x]) for x\
                                  in children_keys]
        return retval

    if vdex:
        vdex_dict = vdex.getVocabularyDict()
    else:
        vdex_dict = model.vocab()

    keys = vdex_dict.keys()
    keys.sort()
    for key in keys:
        retval.append(convert(key, vdex_dict[key]))
    return retval


def manager_from_file(data):
    """
        Return a vdex manager object from a file like object
        I understand excel, csv and vdex xml files.
        The excel and csv files must be formatted as in
        imsvdex.vdex.VDEXManager Matrices
    """
    first_chunk = data.read(64)
    if isinstance(first_chunk, unicode):
        first_chunk = first_chunk.encode('utf-8')
    data.seek(0)
    try:
        from magic import from_buffer as sniff
        magic = first_chunk and sniff(first_chunk, mime=True)
    except Exception, e:  # pragma: no cover
        magic = 'unsupported on mac or missing library: "%s"' % str(e)
    if magic == 'application/octet-stream':
        matrix = []
        workbook = xlrd.open_workbook(file_contents=data.read())
        worksheet = workbook.sheets()[0]
        #pylint:disable=C0103
        for x in range(worksheet.nrows):
            matrix.append([y.value for y in worksheet.row(x)])
        manager = VDEXManager(matrix=matrix)
    elif magic == 'application/xml':
        manager = VDEXManager(data.read())
    elif magic == 'text/plain':
        matrix = []
        for row in csv.reader(StringIO(data.read().encode('utf-8'))):
            matrix.append([x.decode('utf-8') for x in row])
        manager = VDEXManager(matrix=matrix)
    else:
        raise AttributeError("Unknown file format. magic: \"%s\"" % (magic,))
    return manager


def manager_to_csv(manager):
    """ Convert a vdex manager object to a csv file """
    matrix = manager.exportMatrix()
    retval = StringIO()
    writer = csv.writer(retval)
    for row in matrix:
        row = [x.encode('utf-8') for x in row]
        writer.writerow(row)
    retval.seek(0)
    return retval.read().decode('utf-8')


def manager_to_excel(manager):
    """ Convert a vdex manager object to an excel file """
    matrix = manager.exportMatrix()
    workbook = xlwt.Workbook()
    worksheet = workbook.add_sheet('1')
    #pylint:disable=C0103
    for x, row in enumerate(matrix):
        for y, cell in enumerate(row):
            worksheet.write(x, y, cell)
    retval = StringIO()
    workbook.save(retval)
    retval.seek(0)
    return retval


@view_config(route_name='start', renderer="vdex_editor:vocabulary.pt")
class Vocabulary(object):
    """ View for vocabulary """
    def __init__(self, request):
        self.request = request
        self.context = get_global_store()[self.vocab_id]

    @property
    def vocab_id(self):
        return self.request.matchdict['id']

    @property
    def json_url(self):
        return self.request.route_url('json', id=self.vocab_id)

    @property
    def export_vdex_url(self):
        return self.request.route_url('export.xml', id=self.vocab_id)

    @property
    def export_xls_url(self):
        return self.request.route_url('export.xls', id=self.vocab_id)

    @property
    def export_csv_url(self):
        return self.request.route_url('export.csv', id=self.vocab_id)

    @property
    def save_url(self):
        return self.request.route_url('start', id=self.vocab_id)

    def __call__(self):
        if 'form.save_new' in self.request.params:
            return self.handle_update()
        else:
            return {'view': self,
                    'save_url': self.save_url,
                    'json_url': self.json_url,
                    'export_vdex_url': self.export_vdex_url,
                    'export_xls_url': self.export_xls_url,
                    'export_csv_url': self.export_csv_url}

    def handle_update(self):
        """ Update vocabulary """
        try:
            new_data = base64.decodestring(self.request.params['new_data'])
            self.context.import_vdex(StringIO(new_data))
            global_store = get_global_store()
            global_store[self.vocab_id] = self.context
            update_global_store(global_store)
        except (AttributeError, BaseEncodingError), exc:
            return HTTPInternalServerError(body=str(exc))
        return HTTPFound(location=self.save_url)

    @property
    def csv(self):
        """ Return csv representation of vocabulary """
        if not self.context.data:
            return SAMPLE_CSV
        try:
            return self.context.export_as_csv()
        except VDEXError:
            return 'Could not read data'

    @property
    def rows(self):
        """ Return rows of vocabulary as matrix representation """
        return self.context.get_matrix()


@view_config(route_name="json", renderer="json")
class JSON(object):
    """ JSON Views for vocabulary """
    def __init__(self, context, request):
        self.context = get_global_store()[request.matchdict['id']]
        self.request = request

    def __call__(self):
        if 'preview_tree' in self.request.params:
            return self.handle_preview_tree()
        if 'preview_table' in self.request.params:
            return self.handle_preview_table()
        if 'preview_xml' in self.request.params:
            return self.handle_preview_xml()
        else:
            return vdex_or_model_to_dynatree(model=self.context)

    def handle_preview_tree(self):
        """ Return tree preview of suggested new vocabulary """
        fob = StringIO()
        fob.write(self.request.params['preview'].encode('utf-8').strip())
        fob.seek(0)
        matrix = []
        for row in csv.reader(fob):
            matrix.append(row)
        manager = VDEXManager(matrix=matrix)
        return vdex_or_model_to_dynatree(vdex=manager)

    def handle_preview_table(self):
        """ Return table preview of suggested new vocabulary """
        fob = StringIO()
        fob.write(self.request.params['preview'].encode('utf-8'))
        fob.seek(0)
        matrix = []
        for row in csv.reader(fob):
            matrix.append([x.decode('utf-8') for x in row])
        return matrix

    def handle_preview_xml(self):
        """ Return xml preview of suggested new vocabulary """
        fob = StringIO()
        fob.write(self.request.params['preview'].encode('utf-8'))
        fob.seek(0)
        matrix = []
        for row in csv.reader(fob):
            matrix.append(row)
        manager = VDEXManager(matrix=matrix)
        return base64.encodestring(manager.serialize())


@view_config(renderer="string", route_name="upload")
def convert(request):
    """ Converter helper method"""
    fob = request.params['file'].file
    manager = manager_from_file(fob)
    # We fake the content type to silent the browsers
    request.response_content_type = 'text/html'
    return "<csv>%s</csv><xml>%s</xml>" % (\
                       json.dumps(manager_to_csv(manager).encode('utf-8')),
                       base64.encodestring(manager.serialize()))


@view_config(route_name="export.xml")
def xml_download(request):
    """ XML Download of vocabulary """
    context = get_global_store()[request.matchdict['id']]
    retval = Response(context.export_as_xml())
    retval.content_disposition = 'attachment; filename="%s.xml"' % \
        request.context.__name__
    retval.content_type = 'application/octet-stream'
    return retval


@view_config(route_name="export.xls")
def xls_download(request):
    """ XLS Download of vocabulary """
    context = get_global_store()[request.matchdict['id']]
    retval = Response(context.export_as_excel().read())
    retval.content_disposition = 'attachment; filename="%s.xls"' % \
        request.context.__name__
    retval.content_type = 'application/octet-stream'
    return retval


@view_config(route_name="export.csv")
def csv_download(request):
    """ CSV Download of vocabulary """
    context = get_global_store()[request.matchdict['id']]
    retval = Response(context.export_as_csv())
    retval.content_disposition = 'attachment; filename="%s.csv"' % \
        request.context.__name__
    retval.content_type = 'application/octet-stream'
    return retval


def main(**settings):
    config = Configurator()
    config.add_route('json', pattern='/json/{id}')
    config.add_route('upload', '/upload')
    config.add_route('export.xml', pattern='/export.xml/{id}')
    config.add_route('export.xls', pattern='/export.xls/{id}')
    config.add_route('export.csv', pattern='/export.csv/{id}')
    config.add_route('start', pattern='/{id}')
    config.add_static_view('static', os.path.join(here, 'static'))

    config.scan()
    return config.make_wsgi_app()

if __name__ == '__main__':
    app = main()
    serve(app, host='0.0.0.0')
