#!/usr/bin/env python3

from tornado import websocket, web, ioloop
from tornado.log import enable_pretty_logging; enable_pretty_logging()
from subprocess import PIPE, Popen
import json
import xml.etree.ElementTree as ET
import re
from threading import Thread
import time
import os
from os.path import dirname, realpath, splitext, isfile
import sys
import random
if sys.version_info[0] == 3:
    from queue import Queue, Empty
    import pickle
elif sys.version_info[0] == 2:
    from Queue import Queue, Empty
    import cPickle as pickle
if os.name == 'nt':
    try:
        import win32com.client
        import pythoncom
    except:
        print('SAPI5 initialization failed. To use system TTS, please install pywin32.')

INT32_MAX = 2**31 - 1
SVSFlagsAsync = 1

os.chdir(dirname(realpath(__file__)))
try:
    os.makedirs('data/cache')
except:
    pass


class Config(object):

    config = dict()
    try:
        with open('mecab-translate.conf') as f:
            for l in f.read().splitlines():
                c = [t.strip() for t in l.split('=', 1)]
                if len(c) == 2:
                    config[c[0]] = c[1]
    except:
        pass

    @staticmethod
    def get(variable):
        return Config.config.get(variable)


class TTS(object):

    def __init__(self):
        self.clients = []
        self.voice_choices = []
        self.queue = Queue()
        if 'win32com' not in globals():
            return
        Thread(target=self._background).start()

    def _background(self):
        pythoncom.CoInitialize()
        self.tts = win32com.client.Dispatch("SAPI.SpVoice")
        self.voices = self.tts.GetVoices()
        self.voices = [self.voices.Item(i) for i in range(self.voices.Count)]
        self.voice_choices = [dict(desc=v.GetDescription(), id=i) for i, v in enumerate(self.voices)]
        self.tts.Rate = -5
        self.event_sink = win32com.client.WithEvents(self.tts, TTSEventSink)
        self.event_sink.setTTS(self)
        while True:
            self._speak(self.queue.get(True))

    def _speak(self, text):
        self._speaking = True
        self.tts.Skip("Sentence", INT32_MAX)
        self.tts.Speak(text, SVSFlagsAsync)
        self._pump()

    def speak(self, text):
        while True:
            try:
                self.queue.get(False)
            except Empty:
                break
        self.queue.put(text)

    def get_voice_choices(self):
        return self.voice_choices

    def set_voice(self, voice_id):
        self.tts.Voice = self.voices[voice_id]

    def handle_event(self, event, *args):
        msg = dict(type=event)
        if event == 'end':
            self._speaking = False
        elif event == 'word':
            msg.update(dict(char_pos=args[0], length=args[1]))
        msg = json.dumps(msg)
        for c in self.clients:
            c.write_message(msg)

    def _pump(self):
        skipped = False
        while self._speaking:
            if not skipped and not self.queue.empty():
                self.tts.Skip("Sentence", INT32_MAX)
                skipped = True
            pythoncom.PumpWaitingMessages()
            time.sleep(0.05)


class TTSEventSink(object):

    def __init__(self):
        self.tts = None

    def setTTS(self, tts):
        self.tts = tts

    def OnWord(self, stream_id, stream_pos, char_pos, length):
        self.tts.handle_event('word', char_pos, length)

    def OnEndStream(self, stream, pos):
        self.tts.handle_event('end')


class Mecab(object):

    IPADIC = ['pos', 'pos2', 'pos3', 'pos4', 'inflection_type',
        'inflection_form', 'lemma', 'reading', 'hatsuon']

    UNIDIC = ['pos', 'pos2', 'pos3', 'pos4', 'inflection_type',
        'inflection_form', 'lemma_reading', 'lemma', '_', 'reading']

    def __init__(self):
        self.dictionary_format = Mecab.IPADIC
        dic_f = Config.get('mecab_dictionary_format')
        if dic_f == 'unidic':
            self.dictionary_format = Mecab.UNIDIC
        elif dic_f and dic_f != 'ipadic':
            print('unknown mecab_dictionary_format: {}'.format(dic_f))
        args = ['mecab']
        if Config.get('mecab_dictionary'):
            args += ['-d', Config.get('mecab_dictionary')]
        self.process = Popen(args, stdout=PIPE, stdin=PIPE, bufsize=1)
        self.output = Queue()
        self.t = Thread(target=self._handle_stdout)
        self.t.daemon = True
        self.t.start()

    def analyze(self, text):
        self.process.stdin.write((text + '\n').encode('utf-8'))
        self.process.stdin.flush()

        result = []
        while True:
            line = self.output.get()
            if line == 'EOS':
                break

            try:
                part = dict()
                part['literal'], line = line.split('\t')
                part.update(zip(self.dictionary_format,
                    ['' if i == '*' else i for i in line.split(',')]))
            except Exception as e:
                print(e)
            result.append(part)
        return result

    def _handle_stdout(self):
        for line in iter(self.process.stdout.readline, b''):
            self.output.put(line.decode('utf-8').strip())
        self.process.stdout.close()


class Dictionary(object):

    def __init__(self, dictionary):
        self.dictionary = list(dictionary.items())
        self.dictionary.sort(key=lambda e: e[0])

    def regex_search(self, query):
        results = dict(exact=None, shorter=None, longer=[], regex=[])

        pattern = re.compile(query)

        for w in self.dictionary:
            if pattern.match(w[0]):
                results['regex'].append(w[0])

        return results

    def get(self, key):
        results = dict(exact=None, shorter=None, longer=[])

        if not key:
            return results

        index = self._search_dict(key)

        if index is None:
            # shorter
            shorter = key[:-1]
            while shorter:
                shorter_index = self._search_dict(shorter)
                if shorter_index is not None:
                    results['shorter'] = self.dictionary[shorter_index][1]
                    break
                shorter = shorter[:-1]
            # prepare for longer
            index = self._search_dict(key, False)
            while True:
                if index >= 0:
                    entry = self.dictionary[index]
                else:
                    break
                if not entry[0][0] == key[0]:
                    break
                index -= 1
        else:
            results['exact'] = self.dictionary[index][1]

        while True:
            index += 1
            if index >= len(self.dictionary):
                break
            entry = self.dictionary[index]
            if entry[0] < key:
                continue
            if entry[0].startswith(key):
                results['longer'].append(entry[0])
            else:
                break

        return results

    def _search_dict(self, key, exact=True):
        imax = len(self.dictionary) - 1
        imin = 0

        while imin <= imax:

            imid = int((imin + imax) / 2)

            if self.dictionary[imid][0] == key:
                return imid

            elif self.dictionary[imid][0] < key:
                imin = imid + 1

            else:
                imax = imid - 1

        if not exact:
            return imin


class JMdict_e(object):

    def __init__(self):
        self.dictfile = open('data/JMdict_e', 'rb')
        self.temp_dictionary = dict()
        self.entities = dict()
        self._parse()
        self.dictionary = Dictionary(self.temp_dictionary)
        del self.temp_dictionary

    def get(self, word, regex=False):

        if regex:
            return self.dictionary.regex_search(word)

        res = self.dictionary.get(word)

        if res['exact']:
            res['exact'] = [self._entry(e) for e in res['exact']]
        if res['shorter']:
            res['shorter'] = [self._entry(e) for e in res['shorter']]

        return res

    def _entry(self, entry):
        ent = self.entities

        entry_obj = dict(words=[], readings=[], translations=[])

        position, length = entry

        self.dictfile.seek(position)
        entry = self.dictfile.read(length)
        jmdict_entity = '|'.join(map(re.escape, ent)).encode('utf-8')
        entry = re.sub(b'&('+jmdict_entity+b');', b'\\1', entry)
        entry = ET.fromstring(entry)

        for k_ele in entry.iter('k_ele'):
            word = dict(inf=[], pri=[])
            word['text'] = k_ele.find('keb').text

            for ke_inf in k_ele.iter('ke_inf'):
                word['inf'].append([ke_inf.text, ent[ke_inf.text]])
            for ke_pri in k_ele.iter('ke_pri'):
                word['pri'].append(ke_pri.text)

            entry_obj['words'].append(word)

        for r_ele in entry.iter('r_ele'):
            reading = dict(inf=[], pri=[], restr=[], nokanji=False)
            reading['text'] = r_ele.find('reb').text

            for re_inf in r_ele.iter('re_inf'):
                reading['inf'].append([re_inf.text, ent[re_inf.text]])
            for re_pri in r_ele.iter('re_pri'):
                reading['pri'].append(re_pri.text)
            for re_restr in r_ele.iter('re_restr'):
                reading['restr'].append(re_restr.text)
            if r_ele.find('re_nokanji') is not None:
                reading['nokanji'] = True

            entry_obj['readings'].append(reading)

        for sense in entry.iter('sense'):
            def _get_tags(name):
                return [t.text for t in sense.iter(name)]

            translation = dict(
                gloss=_get_tags('gloss'),
                stagk=_get_tags('stagk'),
                stagr=_get_tags('stagr'),
                pos=[[pos, ent[pos]] for pos in _get_tags('pos')],
                xref=_get_tags('xref'),
                ant=_get_tags('ant'),
                field=[[field, ent[field]] for field in _get_tags('field')],
                misc=[[misc, ent[misc]] for misc in _get_tags('misc')],
                s_inf=_get_tags('s_inf'),
                lsource=[[ls.attrib.get('{http://www.w3.org/XML/1998/namespace}lang') or
                        (ls.attrib.get('ls_wasei') and 'wasei') or 'eng',
                        ls.text]
                    for ls in sense.iter('lsource')],
                dial=[[dial, ent[dial]] for dial in _get_tags('dial')],
            )

            entry_obj['translations'].append(translation)

        return entry_obj

    def _parse(self):
        print('parsing JMdict_e...')
        start = time.time()

        inside_jmdict = False
        inside_entry = False
        entry_keys = []
        entry_start = 0

        while True:
            line = self.dictfile.readline()
            if not line:
                break

            if not inside_jmdict:
                if line.startswith(b'<!ENTITY'):
                    line = line.decode('utf-8')
                    key, value = re.match('<!ENTITY (.*?) "(.*?)"', line).groups()
                    self.entities[key] = value
                if line == b'<JMdict>\n':
                    inside_jmdict = True
                continue

            if not inside_entry:
                if line == b'<entry>\n':
                    inside_entry = True
                    entry_start = self.dictfile.tell() - len(line)
                continue

            if line[2:5] == b'eb>':
                entry_keys.append(line[5:-7].decode('utf-8'))
                continue

            if line == b'</entry>\n':
                inside_entry = False
                entry_position = (entry_start, self.dictfile.tell() - entry_start)

                for k in entry_keys:
                    if not self.temp_dictionary.get(k):
                        self.temp_dictionary[k] = []
                    self.temp_dictionary[k].append(entry_position)

                entry_keys = []

        print('    parsed in {:.2f} s'.format(time.time() - start))


class Kanjidic2(object):

    def __init__(self):
        self.dicfile = open('data/kanjidic2.xml', 'rb')
        self.dic = dict()
        self._parse()

    def get(self, kanji):
        dic_entry = self.dic.get(kanji)
        if not dic_entry:
            return

        entry = dict(literal=kanji, on=[], kun=[], nanori=[], meaning=[])

        position, length = dic_entry

        self.dicfile.seek(position)
        character = self.dicfile.read(length)
        character = ET.fromstring(character)

        # stroke count, frequency
        misc = character.find('misc')

        stroke_count = misc.find('stroke_count')
        if stroke_count is not None:
            stroke_count = int(stroke_count.text)
        entry['stroke_count'] = stroke_count

        freq = misc.find('freq')
        if freq is not None:
            freq = int(freq.text)
        entry['freq'] = freq

        reading_meaning = character.find('reading_meaning')
        if not reading_meaning:
            return
        rmgroup = reading_meaning.find('rmgroup')
        # meaning
        for meaning in rmgroup.iter('meaning'):
            if meaning.get('m_lang') and meaning.get('m_lang') != 'en':
                continue
            entry['meaning'].append(meaning.text)
        # reading
        for reading in rmgroup.iter('reading'):
            r_type = reading.get('r_type')
            if r_type == 'ja_on':
                entry['on'].append(reading.text)
            if r_type == 'ja_kun':
                entry['kun'].append(reading.text)
        # nanori
        for nanori in reading_meaning.iter('nanori'):
            entry['nanori'].append(nanori.text)

        return entry

    def _parse(self):
        print('parsing kanjidic2...')
        start = time.time()

        inside_character = False
        literal = None
        character_start = 0

        while True:
            line = self.dicfile.readline()
            if not line:
                break

            if not inside_character:
                if line == b'<character>\n':
                    inside_character = True
                    character_start = self.dicfile.tell() - len(line)
                    literal = self.dicfile.readline()[9:-11].decode('utf-8')
                continue

            if line == b'</character>\n':
                inside_character = False
                character_position = (character_start, self.dicfile.tell() - character_start)
                self.dic[literal] = character_position

        print('    parsed in {:.2f} s'.format(time.time() - start))


class Tatoeba(object):

    def __init__(self):
        self.datafile = open('data/wwwjdic.csv', 'rb')
        self.dictionary = dict()
        self.lines = []
        self._parse()

    def search_phrase(self, phrase, max, start, shuffle):
        lines = list(self.lines)
        if shuffle:
            random.shuffle(lines)

        examples = []
        counter = 0
        while counter < start + max and lines:
            file_pos, length = lines.pop(0)
            self.datafile.seek(file_pos)
            line = self.datafile.read(length).decode('utf-8')

            jpn, eng = line.split('\t')[2:4]
            if phrase in jpn:
                if counter >= start:
                    examples.append(dict(jpn=jpn, eng=eng))
                counter += 1

        return examples

    def get(self, headwords, readings):
        for hw in headwords:
            entries = self.dictionary.get(hw)
            headword = hw
            if entries:
                break
        else:
            return []

        def matches(entry):
            reading_matches = None
            for reading in readings:
                if entry[1]:
                    if reading != entry[1]:
                        reading_matches = False
                    elif reading == entry[1]:
                        reading_matches = True
                        break
            if reading_matches == False:
                return False
            return True

        return [
            dict(
                self._entry(entry[0]),
                sense=entry[2] or 0,
                form=entry[3] or headword
            )
            for entry in filter(matches, entries)
        ]

    def _entry(self, file_pos):
        file_pos, length = file_pos

        self.datafile.seek(file_pos)
        line = self.datafile.read(length).decode('utf-8')

        jpn, eng = line.split('\t')[2:4]

        return dict(jpn=jpn, eng=eng)

    def _parse(self):
        print('parsing wwwjdic.csv...')
        start = time.time()

        index_pattern = re.compile(r'([^\(\[\{~]+)(?:\|\d)?(\(.*?\))?(\[\d\d\])?({.*?})?(~)?')

        while True:
            line = self.datafile.readline()
            if not line:
                break

            length = len(line)
            file_pos = self.datafile.tell() - length
            file_pos = (file_pos, length)
            self.lines.append(file_pos)

            indices = line[line.rfind(b'\t') + 1:].decode('utf-8').split()

            for index in indices:
                headword, reading, sense, form, good = index_pattern.match(index).groups()
                if good:
                    if not self.dictionary.get(headword):
                        self.dictionary[headword] = []
                    if reading:
                        reading = reading[1:-1]
                    if sense:
                        sense = int(sense[1:-1])
                    if form:
                        form = form[1:-1]
                    self.dictionary[headword].append((file_pos, reading, sense, form))

        print('    parsed in {:.2f} s'.format(time.time() - start))


class KanjiVGParts(object):

    CACHE = 'data/cache/kanjivgparts.pickle'

    def __init__(self):
        if isfile(KanjiVGParts.CACHE):
            print('loading KanjiVG parts from cache...')
            start = time.time()
            with open(KanjiVGParts.CACHE, 'rb') as f:
                self.kanji = pickle.load(f)
            print('    loaded in {:.2f} s'.format(time.time() - start))
        else:
            self.kanji = dict()
            self._parse()
            with open(KanjiVGParts.CACHE, 'wb') as f:
                pickle.dump(self.kanji, f, pickle.HIGHEST_PROTOCOL)

    def get_parts(self, kanji):
        return list(self.kanji.get(kanji) or [])

    def get_combinations(self, parts):
        combinations = []

        for k in self.kanji:
            if parts.issubset(self.kanji[k]):
                combinations.append(k)

        combinations = list(map(
            lambda k: (k, (kanjidic2.get(k) or dict()).get('freq') or 2501),
            combinations))

        combinations.sort(key=lambda k: k[1])

        return combinations

    def _parse(self):
        print('parsing KanjiVG parts...')
        start = time.time()

        KANJIVG = 'client/kanji'
        NS = {
            'svg': 'http://www.w3.org/2000/svg',
            'kvg': 'http://kanjivg.tagaini.net'
        }

        def get_parts(group):
            parts = []
            for g in group.findall('svg:g', NS):
                element = g.attrib.get('{'+NS['kvg']+'}element')
                if element:
                    parts.append(element)
                else:
                    parts += get_parts(g)
            return parts

        def get_info(f):
            kanji = ET.parse(f).getroot().find('svg:g', NS).find('svg:g', NS)
            char = kanji.attrib.get('{'+NS['kvg']+'}element')
            parts = get_parts(kanji)
            return char, parts

        for f in os.listdir(KANJIVG):
            char, parts = get_info(os.path.join(KANJIVG, f))
            if char:
                self.kanji[char] = set(parts)

        print('    parsed in {:.2f} s'.format(time.time() - start))


class KanjiSimilars(object):

    def __init__(self):
        self.file = 'data/kanji.tgz_similars.ut8'
        self._parse()

    def get(self, kanji):
        similar = list(map(
            lambda k: (k, (kanjidic2.get(k) or dict()).get('freq') or 2501),
            self.similars.get(kanji) or []))
        similar.sort(key=lambda k: k[1])
        return similar

    def _parse(self):
        print('parsing kanji.tgz_similars.ut8...')
        start = time.time()

        self.similars = dict()
        with open(self.file, 'rb') as f:
            lines = f.read().decode('utf-8').splitlines()
        for l in lines:
            l = [k for k in l.split('/') if k.strip()]
            self.similars[l[0]] = l[1:]

        print('    parsed in {:.2f} s'.format(time.time() - start))


class MecabHandler(web.RequestHandler):

    def post(self):
        data = json.loads(self.request.body.decode('utf-8')).strip()
        self.write(json.dumps([mecab.analyze(line) for line in data.splitlines()]))


class JMdict_eHandler(web.RequestHandler):

    def get(self):
        self.set_header('Cache-Control', 'max-age=3600')
        self.set_header('Content-Type', 'application/json')
        query = self.get_query_argument('query').strip()
        regex = True if self.get_query_argument('regex', default='no') == 'yes' else False
        response = jmdict_e.get(query, regex)
        self.write(json.dumps(response))


class Kanjidic2Handler(web.RequestHandler):

    def get(self):
        self.set_header('Cache-Control', 'max-age=3600')
        self.set_header('Content-Type', 'application/json')
        query = self.get_query_argument('query').strip()
        self.write(json.dumps(kanjidic2.get(query)))


class TatoebaHandler(web.RequestHandler):

    def get(self):
        self.set_header('Cache-Control', 'max-age=3600')
        self.set_header('Content-Type', 'application/json')
        query = self.get_query_argument('query').strip().split(',')
        readings = self.get_query_argument('readings', default='').split(',')
        self.write(json.dumps(tatoeba.get(query, readings)))

class PhraseHandler(web.RequestHandler):

    def get(self):
        self.set_header('Cache-Control', 'max-age=0')
        self.set_header('Content-Type', 'application/json')
        query = self.get_query_argument('query').strip()
        max = int(self.get_query_argument('max', default=5))
        start = int(self.get_query_argument('start', default=0))
        shuffle = True if self.get_query_argument(
            'shuffle', default='yes') == 'yes' else False
        self.write(json.dumps(tatoeba.search_phrase(query, max, start, shuffle)))

class KanjiVGPartsHandler(web.RequestHandler):

    def get(self):
        self.set_header('Cache-Control', 'max-age=3600')
        self.set_header('Content-Type', 'application/json')
        query = self.get_query_argument('query').strip()
        self.write(json.dumps(kvgparts.get_parts(query)))


class KanjiVGCombinationsHandler(web.RequestHandler):

    def get(self):
        self.set_header('Cache-Control', 'max-age=3600')
        self.set_header('Content-Type', 'application/json')
        query = self.get_query_argument('query').strip().split(',')
        self.write(json.dumps(kvgparts.get_combinations(set(query))))


class KanjiSimilarsHandler(web.RequestHandler):

    def get(self):
        self.set_header('Cache-Control', 'max-age=3600')
        self.set_header('Content-Type', 'application/json')
        query = self.get_query_argument('query').strip()
        self.write(json.dumps(kanjisimilars.get(query)))


class TTSHandler(web.RequestHandler):

    def get(self):
        voice_id = int(self.get_query_argument('voice_id', -1))
        if voice_id == -1:
            self.write(json.dumps(tts.get_voice_choices()))
        else:
            tts.set_voice(voice_id)

    def post(self):
        text = json.loads(self.request.body.decode('utf-8'))
        tts.speak(text)


class TTSEventHandler(websocket.WebSocketHandler):

    def open(self):
        if self not in tts.clients:
            tts.clients.append(self)

    def on_close(self):
        if self in tts.clients:
            tts.clients.remove(self)


class StaticFileHandler(web.StaticFileHandler):

    def get_content_type(self):
        types = {
            '.html': 'text/html',
            '.svg': 'image/svg+xml',
            '.js': 'application/javascript',
            '.css': 'text/css',
        }
        return (types.get(os.path.splitext(self.absolute_path)[1]) or
            'application/octet-stream')


def get_app():

    return web.Application([
        (r'/mecab', MecabHandler),
        (r'/jmdict_e', JMdict_eHandler),
        (r'/kanjidic2', Kanjidic2Handler),
        (r'/tatoeba', TatoebaHandler),
        (r'/phrase', PhraseHandler),
        (r'/kvgparts', KanjiVGPartsHandler),
        (r'/kvgcombinations', KanjiVGCombinationsHandler),
        (r'/kanjisimilars', KanjiSimilarsHandler),
        (r'/tts', TTSHandler),
        (r'/tts_events', TTSEventHandler),
        (r'/(.*)', StaticFileHandler,
            {'path': 'client', 'default_filename': 'index.html'}),
    ])


if __name__ == '__main__':
    mecab = Mecab()
    jmdict_e = JMdict_e()
    kanjidic2 = Kanjidic2()
    tatoeba = Tatoeba()
    kvgparts = KanjiVGParts()
    kanjisimilars = KanjiSimilars()
    tts = TTS()
    app = get_app()
    app.listen(9874)
    main_loop = ioloop.IOLoop.instance()
    print('done!')
    print('server listening to *:9874')
    main_loop.start()
