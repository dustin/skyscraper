from zope.interface import implements

from twisted.python import log
from twisted.internet import defer
from twisted.words.xish import domish
from wokkel.subprotocols import XMPPHandler, IQHandlerMixin
from twisted.words.protocols.jabber import jid, error
from twisted.words.protocols.jabber.xmlstream import toResponse
from wokkel import disco
from wokkel import generic
from wokkel import data_form
from wokkel.iwokkel import IDisco

from translate import Translate
from languages import Language

import protocol

NS_CMD = 'http://jabber.org/protocol/commands'
CMD = generic.IQ_SET + '/command[@xmlns="' + NS_CMD + '"]'

all_commands = {}

def form_required(orig):
    def every(self, iq, cmd):
        if cmd.firstChildElement():
            form = data_form.Form.fromElement(cmd.firstChildElement())
            return orig(self, iq, cmd, form)
        else:
            form = data_form.Form(formType="form", title=self.name)
            self.fillForm(iq, cmd, form)
            return self.genFormCmdResponse(iq, cmd, form)
    return every

class BaseCommand(object):
    """Base class for xep 0050 command processors."""

    def __init__(self, node, name):
        self.node = node
        self.name = name

    def _genCmdResponse(self, iq, cmd, status=None):

        command = domish.Element(('http://jabber.org/protocol/commands',
                                     "command"))
        command['node'] = cmd['node']
        if status:
            command['status'] = status
        try:
            command['action'] = cmd['action']
        except KeyError:
            pass

        return command

    def genFormCmdResponse(self, iq, cmd, form):
        command = self._genCmdResponse(iq, cmd, 'executing')

        actions = command.addElement('actions')
        actions['execute'] = 'next'
        actions.addElement('next')

        command.addChild(form.toElement())

        return command

    def __call__(self, iq, cmd):
        # Will return success
        pass

class TranslateCommand(BaseCommand):

    def __init__(self):
        super(TranslateCommand, self).__init__('translate',
                                               'Translate some text')

        langopts = [data_form.Option(l[1], l[0])
                    for l in sorted(Language.languages.items())]

        self.infield = data_form.Field(var='in', fieldType='list-single',
                                  options=langopts)
        self.outfield = data_form.Field(var='out', fieldType='list-multi',
                                   options=langopts)
        self.textfield = data_form.Field(var='text', fieldType='text-multi')

    def fillForm(self, iq, cmd, form):
        form.instructions = ["Select the source and target languages."]

        form.addField(self.infield)
        form.addField(self.outfield)
        form.addField(self.textfield)

    def _formatResponses(self, something, iq, cmd, responses):
        form = data_form.Form(formType="result", title='Your Translations')

        for k,v in sorted(responses.items()):
            form.addField(data_form.Field(var=k, value=v, fieldType='text-multi'))

        return self.genFormCmdResponse(iq, cmd, form)

    @form_required
    def __call__(self, iq, cmd, form):
        lin = form.fields['in'].value
        louts = set(form.fields['out'].values)
        text = form.fields['text'].value.encode('utf-8')

        log.msg(u"From %s to %s:  %s" % (lin, louts, unicode(text)))

        deferreds = []
        responses = {}

        def _handleResponse(out, lang):
            responses[lang] = out

        for l in louts:
            try:
                t = Translate(Language(lin.upper()), Language(l.upper()))
                d = t.translate(text)
                deferreds.append(d)
                d.addCallback(_handleResponse, l)
            except:
                log.err()

        dl = defer.DeferredList(deferreds, consumeErrors=1)
        dl.addCallback(self._formatResponses, iq, cmd, responses)

        return dl

for __t in (t for t in globals().values() if isinstance(type, type(t))):
    if BaseCommand in __t.__mro__:
        try:
            i = __t()
            all_commands[i.node] = i
        except TypeError, e:
            # Ignore abstract bases
            log.msg("Error loading %s: %s" % (__t.__name__, str(e)))
            pass

class AdHocHandler(XMPPHandler, IQHandlerMixin):

    implements(IDisco)

    iqHandlers = { CMD: 'onCommand' }

    def connectionInitialized(self):
        super(AdHocHandler, self).connectionInitialized()
        self.xmlstream.addObserver(CMD, self.handleRequest)

    def onCommand(self, iq):
        log.msg("Got an adhoc command request")
        cmd = iq.firstChildElement()
        assert cmd.name == 'command'

        if cmd.getAttribute('action') == 'cancel':
            log.msg("Canceled")
        else:
            return all_commands[cmd['node']](iq, cmd)

    def getDiscoInfo(self, requestor, target, node):
        info = set()

        if node:
            info.add(disco.DiscoIdentity('automation', 'command-node'))
            info.add(disco.DiscoFeature('http://jabber.org/protocol/commands'))
        else:
            info.add(disco.DiscoFeature(NS_CMD))

        return defer.succeed(info)

    def getDiscoItems(self, requestor, target, node):
        myjid = jid.internJID(protocol.msg_prot.jid)
        return defer.succeed([disco.DiscoItem(myjid, c.node, c.name)
                              for c in all_commands.values()])

