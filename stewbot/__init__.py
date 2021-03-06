# -*- coding: utf-8  -*-
#######################################################
##	Stewardbot
##	Abstracts bot behaviour, interfacing with IRC and web classes.
#######################################################
import os, sys
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.dirname(__file__) + '/components/modules')

import copy # shallow copy objects
import re   # regex
from stewbot.DefaultSettings import ACCESS_WHITELISTED, ACCESS_OPERATOR
from stewbot.components.Bash       import Bash       # !bash - random quotes
from stewbot.components.BaseClass  import BaseClass
from stewbot.components.CommandParser import CommandParser
from stewbot.components.Documentation import Documentation
from stewbot.components.IRC        import IRC
from stewbot.components.Wikimedia  import Browser    # web interface, listing wikis, handling prefixes, etc

###################
## Stewardbot class
###################
class Stewardbot( BaseClass ):
	#############################################################################################################
	##	Constructor
	##	Initializes properties & settings, instantiates required classes.
	#############################################################################################################
	def __init__( self, server, port, nick, user, password, channels, ssl, logger, config, documentation, exceptionLogger = None ):
		BaseClass.__init__( self, logger )
		self.__name__ = 'Stewardbot'
		self.trace(overrides = {'password':'<<hidden>>'})

		#############
		## Commands
		#############
		self.irc_commands = config.irc.commands

		#############
		## Configuration
		#############
		# exception logger
		self.config = config
		self.exceptionLogger = (exceptionLogger if exceptionLogger is not None else logger)

		# default runtime config
		self.options = {
			'confirm_all':config.irc.confirm_all
		}

		self.irc_commands_help = documentation

		# irc users
		self.users = config.irc.wiki_names_by_level
		self.wiki_names = config.irc.wiki_names
		self.INDEX_USERS = ACCESS_WHITELISTED
		self.INDEX_OPERATORS = ACCESS_OPERATOR

		#############
		## Classes
		#############
		self.help = Documentation( documentation, logger = logger )
		self.bash = Bash(logger = logger)

		self.parser = CommandParser(
			commands       = config.irc.commands_by_level,
			callback       = self,
			users          = config.irc.users_by_level,
			banned         = config.irc.ignore_masks,
			command_prefix = config.irc.command_prefix,
			command_delimiter = config.irc.command_delimiter,
			no_commit_commands = config.irc.commands_nocommit,
			logger          = logger,
			handle_commit  = config.irc.handle_commit
		)

		self.browser = Browser(
			username      = config.web.user,
			password      = config.web.password,
			user_agent    = config.web.user_agent,
			max_api_items = config.web.max_api_items,
			default_base_url = config.web.default_base_url,
			logger          = logger
		)
		self.browser.login()

		self.irc = IRC(
			server   = server,
			port     = port,
			nick     = nick,
			user     = user,
			password = password,
			chans    = channels,
			ssl      = ssl,
			default_quit_reason = config.irc.quit_reason,
			callback_pubmsg     = self.onPublicMessage,
			logger          = logger
		)
		self.connect()


	#######################################################
	##	IRC wrappers
	#######################################################
	# connection
	def connect( self ):
		self.irc.connect()
	def reset( self, msg = None ):
		self.irc.reset( msg )
	def disconnect( self, msg = None ):
		self.irc.disconnect( msg )
	def processForever( self ):
		self.irc.processForever()

	# messages
	def sendMessage( self, chan, nick, msg ):
		self.irc.sendMessage( chan, nick, msg )
	def sendPrivateMessage( self, nick, msg ):
		self.irc.sendPrivateMessage( nick, msg )

	def respond( self, data, msg, dot = True, nick = True ):
		self.trace()
		self.irc.sendMessage( data.channel, data.nick if nick else None, u'%s.' % self.Decode(msg) if dot else msg )
	def respondPrivately( self, data, msg ):
		self.irc.sendPrivateMessage( data.nick, msg )


	#######################################################
	##	Error handling
	#######################################################
	###################
	##	 Send syntax error to channel if condition failed, return boolean failed
	###################
	def syntaxError( self, data, msg = None, count = None, condition = None ):
		self.trace()
		# no limit, error by default
		if count is None and condition is None:
			condition = True

		# error on argument count
		elif count is not None:
			(min, max) = (count, count) if self.isInt(count) else (count[0], count[1])
			count = len(data.args)
			condition = (count < min or count > max)
			msg = 'need more arguments' if count < min else 'too many arguments'

		# evaluate condition
		else:
			condition = not condition

		# return result
		if condition:
			self.logger.Log('>> %s' % data.command)
			self.respond( data, '%s; help says %s' % (msg or 'invalid syntax', self.help.get([data.command])) )
			return True
		else:
			return False


	#############################################################################################################
	##	Parse IRC input
	#############################################################################################################
	###################
	##	Handle public message
	###################
	def onPublicMessage( self, data ):
		self.trace()
		try:
			self.parser.handle( data )
		except SystemExit:
			raise
		except KeyboardInterrupt:
			self.sendMessage( data.channel, None, 'halted handling (requested by terminal operator).' )
		except:
			# log exception & dump bot state
			(summary, detailed) = self.HandleException()

			dump = (
				'\n'
				+ '==Exception==\n'
				+ detailed
				+ '\n\n==Last page loaded=='
				+ self.browser.last_url
				+ '\n\n'
				+ self.browser.text
			)
			self.exceptionLogger.Log(dump)
			if self.exceptionLogger.GetLocationString() is not self.logger.GetLocationString():
				self.logger.Log("Exception details sent to exception dump log (%s)" % self.exceptionLogger.GetLocationString())

			# notify IRC users
			irc_error = 'An unhandled exception has occurred: %s.' % summary
			irc_text  = 'Exception details have been sent to the log (%s)' % self.logger.GetLocationString()
			if self.exceptionLogger.GetLocationString() is not self.logger.GetLocationString():
				irc_text += " and the exception dump log (%s)" % self.exceptionLogger.GetLocationString()
			self.respond(data, irc_error, dot = False)
			self.respond(data, irc_text, nick = False)


	###################
	## Handle unhandled commands
	###################
	def handle_None( self, data ):
		self.trace()
		self.respond( data, 'UNHANDLED EXCEPTION: no command handler for %s' % data.command )


	###################
	## Handle errors
	###################
	def handle_Error( self, data ):
		self.trace()

		# security exception: !cancel own request
		self.logger.Log("%s == %s" % (data.flag, self.parser.NOT_ALLOWED))
		self.logger.Log(data.command)

		if data.flag in (self.parser.CANNOT_COMMIT, self.parser.NOT_ALLOWED) and data.command == 'cancel':
			if self.syntaxError( data, count=[1,2] ):
				return

			if self.isInt( data.args[0] ):
				try:
					queued = self.parser.peekQueue( int(data.args[0]) )
					if data.host == queued.host:
						self.handle_cancel( data )
						return
				except KeyError:
					self.respond( data, "There is no commit id %s" % data.args[0] )
					return
			self.respond( data, "You can only cancel your own commands" )

		# display error
		self.respond( data, self.parser.explain(data) )


	###################
	## Handle command queued
	###################
	def handle_Queued( self, data ):
		self.trace()
		self.respond( data, 'commit id %s (restricted command, see \'!help commit\' or \'!help cancel\')' % data.commit_id )


	################################
	## !commit or !cancel
	################################
	def handle_commit( self, data ):
		self.trace()
		return self.handle_commitOrCancel( data, commit = True )
	def handle_cancel( self, data ):
		self.trace()
		return self.handle_commitOrCancel( data, commit = False )

	def handle_commitOrCancel( self, data, commit = False ):
		self.trace()

		# validate arguments
		(ID, OPTION) = (0, 1)
		if self.syntaxError( data, count=[1,2] ):
			return

		# parse options
		if OPTION in data.args:
			data.args[OPTION] = data.args[OPTION].lower()

			if data.args[OPTION] not in ('quiet', 'verbose'):
				self.respond( data, '"%s" is not a valid option, must be one of [quiet, verbose]' % data.args[OPTION] )
				return
			quiet   = (data.args[OPTION] == 'quiet')
			verbose = (data.args[OPTION] == 'verbose')
		else:
			quiet   = False
			verbose = False

		# get list of commit IDs
		(ids, not_queued) = self.parser.parseQueueId( data.args[ID] )
		if not len(self.parser.listQueued()):
			self.respond( data, 'there are no queued commands' )
			return
		if not len(ids):
			self.respond( data, 'there are no queued commands with the given commit ids' )
			return
		if len(not_queued) > 0:
			self.respond( data, 'skipped non-queued commands [%s]' % ', '.join([str(v) for v in not_queued]) )

		# process each id
		for id in ids:
			# commit
			if commit:
				queue = self.parser.commit( id )
				if verbose:
					self.respond( queue, 'committed #%s: !%s %s' % (id, queue.command, ' > '.join(queue.args)), nick = False )
			else:
				queue = self.parser.cancel( id )
				if not quiet:
					self.respond( queue, 'your "%s" command was cancelled' % queue.command )

		# report once if quiet
		if quiet:
			self.respond( data, 'done' )


	###################
	##	!reset & !exit
	###################
	def handle_reset(self, data):
		self.respond( data, 'Acknowledged; resetting IRC connection, returning to default site, and clearing login data' )
		self.browser.reset()
		self.reset( data.args[0] if len(data.args) else None )

	def handle_exit( self, data ):
		self.sendMessage( data.channel, None, self.bash.exitMessage() )
		self.disconnect( data.args[0] if len(data.args) else None )


	###################
	##	Default data argument value
	###################
	def defaultArg( self, data, index, value ):
		if not index in data.args:
			if len(data.args) < index:
				raise self.Error, 'insufficient arguments to default at index %s' % str(index)
			data.args.append( value )


	#############################################################################################################
	##	Global / wiki-agnostic commands
	#############################################################################################################
	###################
	##	CentralAuth commands
	###################
	def handle_lock( self, data ):
		self.handleCentralAuthCommand( data )
	def handle_unlock( self, data ):
		self.handleCentralAuthCommand( data )
	def handle_hide( self, data ):
		self.handleCentralAuthCommand( data )
	def handle_unhide( self, data ):
		self.handleCentralAuthCommand( data )
	def handle_lockhide( self, data ):
		self.handleCentralAuthCommand( data )
	def handle_globaloversight( self, data ):
		self.handleCentralAuthCommand( data )
	def handleCentralAuthCommand( self, data ):
		self.trace()

		# unpack
		(command, args) = (data.command, data.args)
		(USER, REASON) = (0, 1)

		# validate arguments
		if self.syntaxError( data, count=[1,2] ):
			return

		# prepare centralauth values
		lock   = True if command in ['lock', 'hide', 'lockhide', 'globaloversight'] else False if command=='unlock' else None
		hide   = True if command in ['hide', 'lockhide'] else False if command=='unhide' else None
		globalOversight = True if command == 'globaloversight' else None

		# prepare reason
		if len(args) <= REASON:
			if lock or hide:
				args.append( self.config.web.default_ca_reason )
			else:
				args.append('')

		# dispatch
		try:
			self.browser.centralAuth(
				user   = args[USER],
				reason = args[REASON],
				lock   = lock,
				hide   = hide,
				oversightLocal = globalOversight
			)
			if self.options['confirm_all']:
				self.respond( data, 'done' )
		except self.Error, error:
			self.respond( data, error )


	###################
	##	!activity
	###################
	def handle_activity( self, data ):
		self.trace()
		args = data.args
		WIKI = 0

		# validate args
		if self.syntaxError( data, 'invalid argument count', count=1 ):
			return
		if args[WIKI] == 'enwiki':
			self.respond( data, 'last edit, sysop action, bureaucrat action, and checkuser edit: liek two seconds ago' )
			return

		# query stewardry API
		try:
			url = 'http://toolserver.org/~pathoschild/stewardry/?%s' % self.UrlEncode( {'wiki':args[WIKI]} )
			self.browser.load( url = url, parameters = {'api':'1'}, parse_as = 'xml', GET = True )
		except self.Error, e:
			self.respond( data, e )
			return
		except:
			self.respond( data, 'An error occurred while querying the Stewardry API: %s' % self.HandleException()[0] )
			return

		# fetch result
		string = self.browser.parsed.getElementsByTagName( 'string' )[0].childNodes[0].nodeValue
		self.respond( data, '%s. For per-user details, see <%s>' % (string, url) )


	###################
	##	!bash
	###################
	def handle_bash( self, data ):
		self.trace()
		args = data.args
		ARG = 0

		# validate
		if self.syntaxError( data, count=[0, 1] ):
			return

		# !bash (random)
		if not len(args):
			self.respond( data, self.bash.stringOrRandom("<%s> %s" % (data.nick, data.text), 0.02), dot = False )

		# !bash > id
		elif self.isInt( args[ARG] ):
			self.respond( data, self.bash.get(args[ARG]), dot = False )

		# !bash > search
		else:
			self.respond( data, self.bash.find(args[ARG]), dot = False )


	###################
	##	!config
	###################
	def handle_config( self, data ):
		self.trace()
		args = data.args
		(OPTION, VALUE) = (0, 1)

		# validate arguments
		if self.syntaxError( data, count = 2 ):
			return
		if self.syntaxError( data, '"%s" is not a recognized config option' % args[OPTION], condition=args[OPTION] in self.options.keys() ):
			return
		if self.syntaxError( data, '"%s" is not a recognized value for config option "%s"' % (args[VALUE], args[OPTION]), condition=args[VALUE] in ['0', '1'] ):
			return

		# process request
		else:
			# restricted options
			args[OPTION] = args[OPTION].lower()
			if args[OPTION] in ('confirm_all'):
				self.options[args[OPTION]] = int( args[VALUE] )
				self.respond( data, 'okay' )
			else:
				self.respond( data, 'UNCAUGHT ERROR: your command passed validation, but could not be processed' )
				return


	###################
	##	!getblocks
	###################
	def handle_getblocks( self, data ):
		self.trace()
		args = data.args
		TARGET = 0

		##########
		## Initialize & validate
		##########
		if self.syntaxError( data, count = 1 ):
			return

		try:
			(user, wiki) = self.splitIdentifier( args[TARGET], allowImplicit = True )
		except self.Error, e:
			self.respond( data, e )
			return

		##########
		## Fetch global
		##########
		out = ''
		if self.isAddress( user ):
			out = 'IP address; '

			# global blocks
			items = self.browser.getGlobalBlocks(user)
			if not len(items):
				out += 'no global blocks'
			else:
				gblocks = []
				for block in items:
					target = block['address']
					expiry = block['expiry'][:-4].replace('T', ' ')
					gblocks.append( '%s until %s' % (target, expiry) )
				out += 'affected by global blocks: [%s]' % ', '.join(gblocks)

		else:
			# global account status
			try:
				status = self.browser.getCentralAuthStatus( user )
				out = 'Global account; '

				if status['locked'] or status['hidden'] or status['oversighted']:
					if status['locked']:
						out += 'locked'
						if status['hidden'] or status['oversighted']:
							out += ' and '
					if status['hidden']:
						out += 'hidden'
					elif status['oversighted']:
						out += 'oversighted'
				else:
					out += 'no global restrictions'

			except self.Error:
				out = 'Not a global account'

		##########
		## Fetch local blocks
		##########
		try:
			self.handleAt( wiki )
			items = self.browser.getBlockStatus( user )
			if not len(items):
				out += '; no blocks on %s' % wiki
			else:
				blocks = []
				for block in items:
					target = block['user']
					expiry = block['expiry'][:-4].replace('T', ' ')
					blocks.append( '%s until %s' % (target, expiry) )
				out += '; blocked on %s: [%s]' % (wiki, ', '.join(blocks))
		except self.Error, e:
			self.respond( data, e )
		finally:
			self.unhandleAt()

		##########
		## Send response
		##########
		self.respond( data, out )


	###################
	##	!gblock
	###################
	def handle_gblock( self, data ):
		self.trace()
		args = data.args
		(TARGET, EXPIRY, REASON) = (0, 1, 2)

		# validate
		if self.syntaxError( data, count = 3 ):
			return
		if self.syntaxError( data, '"%s" is not a valid IP address or CIDR range' % args[TARGET], condition=self.isAddress(args[TARGET]) ):
			return

		# submit
		try:
			self.browser.globalBlock(
				address = args[TARGET],
				reason  = args[REASON],
				expiry  = args[EXPIRY]
			)
			if self.options['confirm_all']:
				self.respond(data, 'done')
		except self.Error, e:
			self.respond(data, e)


	###################
	## !gunblock
	###################
	def handle_gunblock( self, data ):
		self.trace()
		args = data.args
		(TARGET, REASON) = (0, 1)

		# validate
		if self.syntaxError( data, count = 2 ):
			return
		if self.syntaxError( data, '"%s" is not a valid IP address or CIDR range' % args[TARGET], condition=self.isAddress(args[TARGET]) ):
			return

		# submit
		try:
			self.browser.global_unblock(
				address = args[TARGET],
				reason  = args[REASON]
			)
			if self.options['confirm_all']:
				self.respond( data, 'done' )
		except self.Error, e:
			self.respond( data, e )


	###################
	## !help
	###################
	def handle_help( self, data ):
		self.trace()
		(command, args) = (data.command, data.args)
		TOPIC = 0

		# normal cases
		try:
			if TOPIC in args and args[TOPIC].lower() == 'config':
				self.respond( data, self.help.get(args), '"%s" is not a recognized configuration option. Type "!help > config" for documentation' % args[TOPIC] )
			else:
				self.respond( data, self.help.get(args) )

		# special cases
		except self.help.NoSuchKeyException:
			args[TOPIC] = args[TOPIC].lower()

			# !help > access
			if args[TOPIC] == 'access':
				self.respond( data, '%s. List of authorized users sent in private query' % self.irc_commands_help['ACCESS'] )
				self.respondPrivately( data, self.irc_commands_help['ACCESSLIST'] )

			# !help > status
			elif args[TOPIC] == 'status':
				channels = ' '.join( self.irc.getChannels() )
				options  = ', '.join( ['%s=%s' % (k,self.options[k]) for k in self.options.keys()] )
				web_stat = self.browser.loggedIn( force_check = True )
				if not web_stat:
					web_stat = '\x0304logged out\x03'
				self.respond( data, ' IRC channels=[ %s ], options={%s}; web loggedin=%s' % (channels, options, re.sub('http://|/w/', '', web_stat)) )

			# no such command
			else:
				self.respond( data, '"%s" is not a recognized help topic' % args[TOPIC] )


	###################
	## !links
	###################
	def handle_links( self, data ):
		self.trace()
		args = data.args
		USER = 0

		# validate arguments
		if self.syntaxError( data, count = 1 ):
			return

		# print relevant links
		if self.isAddress( args[USER] ):
			self.respond( data, 'http://toolserver.org/~pathoschild/stalktoy?target=%s | http://toolserver.org/~luxo/contributions/contributions.php?user=%s&blocks=true | \x0314http://whois.domaintools.com/%s\x03 | \x0304http://meta.wikimedia.org/wiki/Special:GlobalBlock?wpAddress=%s&wpReason=crosswiki+abuse\x03' % ( args[USER], args[USER], args[USER], args[USER] ))
		else:
			self.respond( data, 'http://toolserver.org/~pathoschild/stalktoy?%s | http://toolserver.org/~luxo/contributions/contributions.php?%s | \x0304http://meta.wikimedia.org/wiki/Special:CentralAuth?%s\x03' % (self.UrlEncode({'target':args[USER]}), self.UrlEncode({'user':args[USER], 'blocks':'true'}), self.UrlEncode({'target':args[USER]})) )


	###################
	## !queue
	## !queue id
	###################
	def handle_queue( self, data ):
		self.trace()

		args = data.args
		(ID, ACTION, FIELD, VALUE) = (0, 1, 2, 3)
		arg_len = len( args )

		##########
		## List ids mode
		##########
		if not arg_len:
			ids = self.parser.listQueued()
			if len( ids ):
				self.respond( data, 'uncommitted command ids: [%s]' % ','.join([str(id) for id in ids]) )
			else:
				self.respond( data, 'no uncommitted commands' )
			return

		##########
		## View id mode
		##########
		elif arg_len == 1:
			# validate
			if not self.isInt( args[ID] ):
				self.respond( data, 'queue ID must be numeric' )
				return
			id = int(args[ID])

			if id not in self.parser.listQueued():
				self.respond( data, 'no commit id %s' % id )
				return

			# fetch and respond
			queue = self.parser.peekQueue( id )
			self.respond( queue, 'queue item #%s by [%s > %s > %s], command "%s" with args [%s]' % (id, queue.channel, queue.mask, queue.nick, queue.command, ' > '.join(queue.args)) )

		##########
		## invalid mode
		##########
		else:
			self.syntaxError(data, 'invalid usage', count=4)
			return



	###################
	## !scanedits
	###################
	def handle_scanedits( self, data ):
		self.trace()
		args = data.args
		USER = 0

		# validate arguments
		if self.syntaxError( data, count=1 ):
			return

		# handle IP address
		if self.isAddress( args[USER] ):
			self.respond( data, 'http://toolserver.org/~luxo/contributions/contributions.php?user=%s&blocks=true' % args[USER] )
			return

		# strip identifier
		if args[USER].find('@') != -1:
			(args[USER], wiki) = args[USER].split('@', 1)

		# handle global account
		args[USER] = self.capitalizeFirstLetter( args[USER] )
		try:
			wikis = self.browser.getGlobalDetails( args[USER] )
		except self.Error, e:
			self.respond( data, e )
			return
		count_wikis = len( wikis )

		if not len(wikis):
			self.respond( data, '%s\'s unified accounts have no edits' % args[USER] )
		else:
			path = '/wiki/Special:Contributions?%s' % self.UrlEncode( {'target':args[USER]} )
			total_edits = sum([ wiki['edits'] for wiki in wikis ])
			wikis = sorted( wikis, key = lambda wiki: wiki['edits'], reverse = True ) # order by edits desc

			# list in channel
			if count_wikis < 3:
				self.respond( data, 'Listing %s edits on %s wikis by %s\'s unified accounts..' % (total_edits, count_wikis, args[USER]), nick = False )
				for wiki in wikis:
					url = self.browser.getUrl( prefix = wiki['wiki'], path = path )
					self.respond( data, "%s:  %s edits at http://%s" % (count_wikis, wiki['edits'], url), nick = False )
					count_wikis -= 1

			# regular mode
			elif count_wikis < 20:
				self.respond( data, '%s\'s unified accounts have %s edits on %s wikis, sending links in private query' % (args[USER], total_edits, count_wikis) )
				self.respondPrivately( data, 'Listing %s edits on %s wikis by %s\'s unified accounts..' % (total_edits, count_wikis, args[USER]) )
				for wiki in wikis:
					url = self.browser.getUrl( prefix = wiki['wiki'], path = path )
					self.respondPrivately( data, "%s:  %s edits at http://%s" % (count_wikis, wiki['edits'], url) )
					count_wikis -= 1

			# too many, faster to post online
			else:
				# build text
				text = '<div class="plainlinks">\n edits by global account "%s"\n edits\twiki\n' % args[USER]
				for wiki in wikis:
					url = self.browser.getUrl( prefix = wiki['wiki'], path = path )
					text += ' %s \t [%s %s]\n' % (wiki['edits'], url, wiki['wiki'])
				text += '</div>'

				# keep user up to date
				self.respond( data, '%s\'s unified accounts have %s edits on %s wikis, saving list to wiki page...' % (args[USER], total_edits, count_wikis), dot = False )

				# submit edit
				revid = self.browser.edit(
					title   = 'User:StewardBot/Sandbox',
					summary = '+ [[user:%s|user]] edits' % args[USER],
					text    = text,
					bot     = 1,
					minor   = 1
				)

				# notify user
				self.respond( data, 'http://meta.wikimedia.org/wiki/User:StewardBot/Sandbox?oldid=%s' % revid, dot = False )


	###################
	## !lookup
	###################
	def handle_lookup( self, data ):
		self.trace()
		args = data.args

		# validate args
		if self.syntaxError( data, count=1 ):
			return
		if self.syntaxError( data, 'invalid language code', condition = len(args[0]) ):
			return

		# fetch data
		try:
			details = self.browser.lookupCode( data.args[0] )
		except self.Error, e:
			self.respond( data, e )
			return

		# display data
		for lang in details:
			self.respond( data, u'%s in %s: %s (%s, %s%s)' % (lang['code'], lang['list'], lang['name'], lang['scope'], lang['type'], u'; %s' % lang['notes'] if lang['notes'] else u''), nick = False )


	###################
	## !translate
	###################
	def handle_translate( self, data ):
		self.trace()
		args = data.args

		# validate args
		if self.syntaxError( data, count=[1,3] ):
			return

		# extract args
		count = len( args )
		if count == 3:
			(source, target, text) = (args[0], args[1], args[2])
		elif count == 2:
			(source, target, text) = (args[0], 'en', args[1])
		else:
			(source, target, text) = ('', 'en', args[0])

		# fetch data
		try:
			result = self.browser.translate( source=source, target=target, text=text )
		except self.Error, e:
			self.respond( data, e )
			return

		# display data
		self.respond( data, u'Google claims %s -> %s: %s' % (result['source_lang'], result['target_lang'], result['target_text']) )


	###################
	## !wikiset
	###################
	def handle_wikiset( self, data ):
		self.trace()
		args = data.args
		(ID, WIKIS, REASON) = (0, 1, 2)

		# validate args
		if self.syntaxError( data, count=[2,3] ):
			return
		if self.syntaxError( data, 'invalid wikiset id', condition = self.isInt(args[ID]) and int(args[ID]) in self.config.web.wikiset_ids.keys() ):
			return

		# prepare reason
		self.defaultArg( data, REASON, 'updated' )

		# parse wikis
		wikis = {'+':[], '-':[]}
		cur = '+'
		for wiki in args[WIKIS].split( ',' ):
				# set array
				if wiki[0] == '+':
					cur = '+'
					wiki = wiki.strip( '+- 	' )
				elif wiki[0] == '-':
					cur = '-'
					wiki = wiki.strip( '+- 	' )

				# set value
				wikis[cur].append( wiki )

		# dispatch
		try:
			self.browser.wikiset(
				id        = args[ID],
				add_wikis = wikis['+'],
				del_wikis = wikis['-'],
				reason    = args[REASON]
			)
			if self.options['confirm_all']:
				self.respond( data, 'done' )
		except self.Error, e:
			self.respond( data, e )


	###################
	## !withlist
	###################
	def handle_withlist( self, data ):
		self.trace()
		(command, args) = (data.command, data.args)
		(URL, COMMAND) = (0, 1)

		# validate arguments
		if len(data.args) < 2:
			self.syntaxError( data, count = 2 )
			return
		args[COMMAND] = args[COMMAND].lower()
		if args[COMMAND] not in self.irc_commands:
			self.respond( data, '"%s" is not a recognized command. Type "!help" for documentation' % args[COMMAND] )
			return

		# prepare new data
		data.command = args[COMMAND]
		data.args = args[2:]

		# fetch URL
		try:
			self.browser.load( url = args[URL] )
		except:
			self.respond( data, 'An error occurred while following the URL: %s' % self.HandleException()[0] )
			return

		# strip HTML markup
		response = self.browser.stripHtml( self.browser.text, strip_newlines = False, suppress_text = True )

		# parse into list
		lines = response.splitlines()
		if not len(lines):
			self.respond( data, 'No lines found at specified URL' )
			return

		# print to console, queue commands for !commit
		ids = []
		for line in lines:
			self.logger.Log("######\n%s\n#####" % line)
			if line:
				queue = copy(data)
				queue.args = copy(data.args)
				queue.args.insert( 0, line )
				ids.append( self.parser.queue(queue) )

		# notify users
		str_ids = (','.join([str(id) for id in ids])) if (len(ids) < 10) else ('%s through %s' % (ids[0], ids[len(ids) - 1]))
		self.respond( data, 'Acknowledged, queued commands; need "!commit > all"; commit ids [%s]' % str_ids )


	###################
	## !debug
	###################
	def handle_debug( self, data ):
		self.trace()
		self.respond( data, self.bash.chloe(), dot = False, nick = False )
		raise Exception, "Chloe crash! :D"


	#############################################################################################################
	##	Wiki-specific commands
	#############################################################################################################
	###################
	## !checkuser
	## syntax: !checkuser > user@wiki
	###################
	def handle_checkuser( self, data ):
		self.trace()
		args = data.args
		TARGET = 0

		# validate arguments
		if self.syntaxError( data, count = 1 ):
			return

		# validate authorization
		if data.host in self.users[self.INDEX_OPERATORS].keys():
			requester = self.users[self.INDEX_OPERATORS][data.host]
		else:
			self.respond( data, 'you are not authorized to use this command (you must be an operator).' )
			return

		# set checkuser right
		try:
			(target, wiki) = self.parseAt( args[TARGET], allowGlobal = False, getUsername = True )
			wiki = wiki[0]

			self.browser.setRights(
				username = '%s@%s' % (requester, wiki),
				groups   = {'checkuser':True},
				reason   = 'checking crosswiki abuse',
				allowUnchanged = True
			)
		except self.Error, e:
			self.respond( data, e )
			return

		# display links
		msg = self.browser.getUrl( prefix = wiki )
		msg += 'Special:Checkuser?%s' % self.UrlEncode( {'user':target, 'reason':'checking crosswiki abuse'} )
		self.respond( data, msg )


	###################
	## !stab / !stabhide
	##	syntax: !stab > user or !stab > user > hard
	###################
	def handle_stabhide( self, data ):
		self.handle_stab( data, hide = True, global_hide = True, reblock = True )

	def handle_stab( self, data, hide = False, global_hide = None, reblock = False ):
		self.trace()

		############
		## Process arguments
		############
		(USER, OPTS) = (0, 1)

		# validate & copy
		if self.syntaxError( data, count = [1,2] ):
			return
		if self.syntaxError( data, 'cannot specify @wiki for a global command', condition=data.args[USER].find('@') == -1 ):
			return
		if self.syntaxError( data, 'unknown hide option', condition=(len(data.args) <= OPTS or data.args[OPTS].lower() == 'hard') ):
			return

		# unpack
		user  = data.args[USER]
		hideWhenEdits = (len(data.args) > OPTS and data.args[OPTS].lower() == 'hard')

		############
		## Lock & hide global account
		############
		# determine description
		desc = 'locked and hidden'
		if not hide:
			desc = 'locked'

		# implement
		try:
			if self.browser.centralAuth(
				user   = user,
				reason = 'crosswiki abuse<!--[[StewardBot|bot]]-->',
				lock   = True,
				hide   = global_hide,
				ignoreUnchanged = True
			):
				self.respond( data, '%s, scanning local accounts..' % desc )
			else:
				self.respond( data, 'already %s, scanning local accounts..' % desc )
		except self.Error, e:
			self.respond( data, e )
			return

		############
		## Scan & block(hide) local accounts
		############
		# fetch list of local accounts
		try:
			wikis = self.parseAt( '%s@global' % user, allowGlobal = True )
			count_wikis = len( wikis )
		except self.Error, e:
			self.respond( data, e )
			return

		path = 'Special:Contributions?%s' % self.UrlEncode( {'target':user} )

		# scan each account, block/blockhide
		for wiki in wikis:
			try:
				self.handleAt( wiki )

				##########
				## Collect account details
				##########
				# edit counts
				_counts = self.browser.countUserEdits( user )
				(edits, top_edits, new_pages, unreverted) = (_counts['edits'], _counts['top'], _counts['new'], _counts['unreverted'])
				blocked = skipped = False

				# block status
				curBlock = self.browser.getBlockStatus( user )
				curBlock = False if (len(curBlock) == 0) else curBlock[0]
				curHidden = curBlock and curBlock['hidden']

				# report text
				msgPrefix = '[%s:%s]' % (count_wikis, wiki)
				notes = ''
				if unreverted:
					notes = ' (OH NOES! %s unreverted %s detected [total %s]: <%s>)' % (
						unreverted,
						'edits & creations' if top_edits and new_pages else 'edits' if top_edits else 'pages',
						edits,
						'%s%s' % (self.browser.getUrl(prefix = wiki), path)
					)
				elif edits:
					notes = ' (%s edits)' % edits

				# block options
				if hide and (not edits or hideWhenEdits):
					result   = 'blockhidden'
					reason   = 'crosswiki abuse<!--[[m:SH#lock|globally locked & hidden]]; [[m:User:StewardBot|about bot]]-->'
					reblock  = True
					hidename = True
				else:
					result   = 'blocked'
					reason   = 'crosswiki abuse<!--[[m:SH#lock|globally locked]]; [[m:User:StewardBot|about bot]]-->'
					reblock  = reblock
					hidename = False

				##########
				## Execute
				##########
				if not hide and wiki in ['enwikibooks']:
					result = 'no block by local request'
					skipped = True

				elif curHidden and curHidden == hide:
					result = 'already %s' % result
					skipped = True

				elif curHidden:
					result = 'account is hidden, skipped'
					skipped = True

				else:
					if not self.browser.block(
						user    = user,
						expiry  = 'never',
						reason  = reason,
						noemail = True,
						hidename= hidename,
						allowusertalk = False,
						autoblock = True,
						reblockIfChanged = True,
						reblock = reblock
					):
						result = 'already %s' % result
						skipped = True

				##########
				## Execute
				##########
				resColor = '15' if skipped else ''
				noteColor = '04' if (top_edits or new_pages or blocked) else '' if edits else '15'
				self.respond( data, '\x03%s%s %s\x03%s%s\x03' % (resColor, msgPrefix, result, noteColor, notes), nick = False )
			except self.Error, e:
				self.respond( data, '%s %s %s' % (msgPrefix, wiki, e), nick = False )
			finally:
				count_wikis -= 1
				self.unhandleAt()


	###################
	## !blockhide (uses !block)
	## syntax: !blockhide > user > reason
	###################
	# block user, and hide them
	def handle_blockhide( self, data ):
		# validate
		if self.syntaxError( data, count = [1,2] ):
			return

		# convert arguments
		self.defaultArg( data, 1, 'crosswiki abuse, globally locked & hidden' )
		data.args = [data.args[0], 'never', data.args[1]]

		# pass to block function
		self.handle_block( data, hidename = True, reblock = True )


	###################
	## !block
	##	syntax: !block > user > expiry > reason
	###################
	def handle_block( self, data, hidename = False, reblock = False ):
		self.trace()
		(USER, EXPIRY, REASON) = (0, 1, 2)
		args = data.args

		# validate args
		if self.syntaxError( data, count=[1,3] ):
			return

		# set expiry & reason
		self.defaultArg( data, EXPIRY, 'never' )
		self.defaultArg( data, REASON, 'crosswiki abuse' )
		args[EXPIRY] = args[EXPIRY].lower()

		# fetch list of local accounts
		try:
			(user, wikis) = self.parseAt( args[USER], allowGlobal = True, getUsername = True, allowImplicit = False )
			count = len( wikis )
		except self.Error, e:
			self.respond( data, e )
			return

		# dispatch queries
		if count > 1:
			self.respond( data, 'blocking "%s" on %s %s..' % (user, count, ('wiki' if count == 1 else 'wikis')) )

		action = 'blocked & hidden' if hidename else 'blocked'
		for wiki in wikis:
			try:
				self.handleAt( wiki )
				self.browser.block(
					user    = user,
					expiry  = args[EXPIRY],
					reason  = args[REASON],
					noemail = True,
					hidename = hidename,
					reblock = reblock
				)
				self.respond( data, '[%s%s] %s' % ('%s:' % count if (count > 1) else '', wiki, action) )
			except self.Error, e:
				self.respond( data, '[%s%s] %s' % ('%s:' % count if (count > 1) else '', wiki, e) )
			finally:
				count -= 1
				self.unhandleAt()

	###################
	## !delete
	## 	syntax: !delete > title > reason
	###################
	def handle_delete( self, data ):
		self.trace()
		(TITLE, REASON) = (0, 1)

		# parse args
		if self.syntaxError( data, count=2 ):
			return
		data.args[REASON] += ' <!--[[m:StewardBot|bot]]-->'

		try:
			(title, wiki) = self.parseAt( data.args[TITLE], allowGlobal = False, getUsername = True, allowImplicit = False )
			wiki = wiki[0]
		except self.Error, e:
			self.respond( data, e )
			return

		# execute
		try:
			self.handleAt( wiki )
			self.browser.delete(
				title  = title,
				reason = data.args[REASON]
			)
			self.respond( data, '[%s] deleted' % wiki )
		except self.Error, e:
			self.respond( data, '[%s] %s' % (wiki, e) )
		finally:
			self.unhandleAt()


	###################
	## !unblock
	##	syntax: !unblock > user > reason
	###################
	def handle_unblock( self, data ):
		self.trace()
		args = data.args
		(USER, REASON) = (0, 1)

		# validate args
		if self.syntaxError( data, count = 2 ):
			return

		# get wikis
		try:
			(user, wikis) = self.parseAt( args[USER], allowGlobal = True, getUsername = True )
			count = len( wikis )
		except self.Error, e:
			self.respond( data, e )
			return

		# dispatch queries
		if count > 1:
			self.respond( data, 'unblocking "%s" on %s %s..' % (user, count, ('wiki' if count == 1 else 'wikis')) )

		for wiki in wikis:
			try:
				self.handleAt( wiki )
				self.browser.unblock(
					user    = user,
					reason  = args[REASON]
				)
				self.respond( data, '[%s%s] done' % ('%s:' % count if (count > 1) else '', wiki) )
			except self.Error, e:
				self.respond( data, '[%s%s] %s' % ('%s:' % count if (count > 1) else '', wiki, e) )
			finally:
				count -= 1
				self.unhandleAt()



	###################
	## !setrights
	##	syntax: !setrights > user > +right1,right2,-right3 > reason
	###################
	def handle_setrights( self, data ):
		self.trace()
		args = data.args
		(USER, GROUPS, REASON) = (0, 1, 2)

		# validate args
		if self.syntaxError( data, count = [2,3] ):
			return
		self.defaultArg( data, REASON, '' )
		args[GROUPS] = args[GROUPS].lower()

		# validate prefix
		try:
			(user, wiki) = self.parseAt( args[USER], allowGlobal = False, allowImplicit = False, getUsername = True )
			wiki = wiki[0]
		except self.Error, e:
			self.respond( data, e )
			return

		# set groups
		groups  = {}
		cur_val = '1'
		for group in args[GROUPS].split(','):
			# rm extra whitespace, set mode
			group = group.strip()
			if group[0] == '+':
				cur_val = True
				group = group.lstrip('+- 	')
			elif group[0] == '-':
				cur_val = False
				group = group.lstrip('+- 	')

			# set value
			groups[group] = cur_val

		# dispatch
		try:
			target = '%s@%s' % (user, wiki)
			self.browser.setRights( target, groups, args[REASON] )
			if self.options['confirm_all']:
				self.respond( data, 'done' )
		except self.Error, e:
			self.respond( data, e )


	###################
	## !showrights
	##	syntax: !showrights > user
	###################
	def handle_showrights( self, data ):
		self.trace()

		# unpack
		args = data.args
		USER = 0

		#validate args
		if self.syntaxError( data, count = 1 ):
			return

		# fetch local groups
		try:
			# parse name@wiki
			(user, wiki) = self.parseAt( args[USER], allowGlobal = False, getUsername = True )
			wiki = wiki[0]

			# fetch groups
			self.handleAt( wiki )
			local_groups = self.browser.getRights( user )
			global_groups = self.browser.getGlobalRights( user )

			# report
			if not len( local_groups ) and not len( global_groups ):
				self.respond( data, '%s is in no %s or global groups' % (user, wiki) )
			else:
				msg = '%s@%s is in local groups [%s]' % (user, wiki, ', '.join( local_groups )) if len( local_groups ) else '%s@%s is in no local groups' % (user, wiki)
				msg += ', and global groups [%s]' % ', '.join( global_groups ) if len( global_groups ) else ', and no global groups'
				self.respond( data, msg )

		except self.Error, e:
			self.respond( data, '"%s": %s' % ('%s@%s' % (user, wiki) if wiki else args[USER], e) )
			return
		finally:
			self.unhandleAt()


	#############################################################################################################
	##	IRC -> Browser interface
	#############################################################################################################
	###################
	## Given user@identifier, return valid (user, db-prefix)
	###################
	def splitIdentifier( self, identifier, allowImplicit = False ):
		self.trace()

		# user
		identifier = self.capitalizeFirstLetter(identifier)
		if identifier.find('@') == -1:
			if allowImplicit:
				return identifier, 'metawiki'
			else:
				raise self.Error, 'implicit "@metawiki" not permitted in this context'

		# user@wiki
		try:
			(user, wiki) = identifier.split( '@', 1 )
			if wiki != 'global':
				self.browser.getUrl( prefix = wiki )
			return user, wiki
		except self.Error, e:
			raise self.Error, e


	###################
	## Given user@identifier, return list of wikis
	##################
	def parseAt( self, identifier, allowGlobal = False, getUsername = False, allowImplicit = True ):
		self.trace()

		# name@identifier
		(user, wiki) = self.splitIdentifier( identifier, allowImplicit = allowImplicit )
		if wiki == 'global':
			if allowGlobal:
				wikis = [wiki['wiki'] for wiki in self.browser.getGlobalDetails( user, True )]
				if getUsername:
					return user, wikis
				else:
					return wikis
			else:
				raise self.Error, '\'global\' is not allowed in this context'
		else:
			if getUsername:
				return user, [wiki]
			else:
				return [wiki]


	###################
	## Given a VALIDATED identifier, sets current wiki to corresponding wiki
	###################
	def handleAt( self, identifier ):
		self.trace()
		self.browser.setBaseUrl( self.browser.getUrl(prefix = identifier, path='') )


	###################
	## Returns to default wiki
	###################
	def unhandleAt( self ):
		self.trace()
		self.browser.resetBaseUrl()
