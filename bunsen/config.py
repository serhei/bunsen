# Bunsen configuration
# Copyright (C) 2019-2022 Red Hat Inc.
#
# This file is part of Bunsen, and is free software. You can
# redistribute it and/or modify it under the terms of the GNU Lesser General
# Public License (LGPL); either version 3, or (at your option) any
# later version.

import re
from pathlib import Path
from configparser import ConfigParser

from bunsen.utils import *

# BunsenOptions fields that should not be overwritten by actual options:
_options_base_fields = {
    '_bunsen',
    'script_name',
    'usage_str',
    'expected_groups',
    'sources',
    '_delayed_config',
    '_unknown_args',
    '_required_args',
    '_positional_args',
    '_cmdline_required_args',
    '_cmdline_optional_args',
}

cmdline_arg_regex = re.compile(r"(?P<prefix>--)?(?:(?P<keyword>[0-9A-Za-z_-]+)=)?(?P<arg>.*)")
# XXX Format for compact command line args.

# TODOXXX Proper usage of cmdline_err(fatal=True|False)
class BunsenOptions:
    """Collects options for a Bunsen analysis script invocation.

    Each option has an internal name and possibly some external names
    used to specify that option in different configuration sources
    (command line arguments, config files, environment variables, and
    CGI invocations). The internal name of the option is the name
    of the BunsenOptions instance variable storing the option's value.

    Attributes:
        script_name (str): Name of the analysis script or command.
        usage_str (str): Explanatory string which will be part of the
            usage message output by print_help().
        expected_groups (set): Option groups that will be used by this script.
            Used to generate a usage message in print_help().
        sources (map): Identifies the configuration source of each option.
    """

    # TODO: Wishlist:
    # - Support for chaining commands (one command produces JSON as output to next command).
    # - Support for generating shell autocompletions.
    # - Support for providing options as JSON (parse_json_query e.g. for REST API).

    # XXX The following is weird but IMO preferable to defining 13 class
    # variables to track the metadata defining each option.

    _options = set()
    # Set of internal_name for all defined options.

    _option_names = {}
    # Maps (external_name, option_type) -> internal_name.
    # See docstring for option_name() for details.

    _option_info = {}
    # Maps (internal_name, attr_or_flag) -> value
    # See docstring for option_info() for details.

    _option_groups = {}
    # Maps group_name -> set(internal_name).

    _negated_options = {}
    # Maps cmdline name of negated option to internal name of the original option.

    @classmethod
    def option_name(cls, external_name, option_type):
        """Returns the internal name of a specified option, or
        None if the external name does not reference an option.

        Args:
            external_name (str): External name of the option.
            option_type (str): One of 'cmdline', 'cmdline_short',
                'env', 'cgi', or 'config'.
        """
        if (external_name, option_type) not in cls._option_names:
            return None
        return cls._option_names[external_name, option_type]

    @classmethod
    def option_names(cls, option_type):
        """Iterate all external names of a specified option type.

        Args:
            option_type (str): One of 'cmdline', 'cmdline_short',
                'env', 'cgi', or 'config'.

        Yields:
            (external_name, internal_name)
        """
        for k, internal_name in cls._option_names.items():
            external_name, k_type = k
            if k_type != option_type: continue
            yield external_name, internal_name

    @classmethod
    def option_info(cls, internal_name, attr_or_flag_name):
        """Returns the specified attribute of a specified option.

        Permitted attributes:
        - default_value: default value of the option.
        - help_string: info about the option, used by print_help().
        - help_cookie: placeholder for the option value, used by print_help().

        Permitted flags (value is boolean):
        - nonconfig: the option may not be specified in a config file.
        - boolean_flag: the option controls a boolean flag.
        - accumulate: multiple flags specifying the option will accumulate
          into a list. New options are appended to the end if low priority,
          at the start if high priority, e.g. "o=c,d -oa -ob => [a,b,c,d]".

        TODO: Options with boolean_flag+accumulate should accumulate into
        a number, e.g. -vvvv => -v4.

        Args:
            internal_name (str): Internal name of the option.
            attr_or_flag_name (str): One of the attribute or flag
                names specified above.
        """
        #assert internal_name in cls._options
        if internal_name not in cls._options:
            return None
        return cls._option_info[internal_name, attr_or_flag_name]

    @classmethod
    def option_group(cls, group_name):
        """Returns a set of internal names of options in specified group."""
        return cls._option_groups(group_name)

    @classmethod
    def _add_option_name(cls, option_type, external_name, internal_name,
                        override=False):
        if external_name is None: return
        cls._option_names[external_name, option_type] = internal_name

    @classmethod
    def add_option(cls, internal_name, group=None,
                   cmdline=None, cmdline_short=None,
                   env=None, cgi=None, config=None,
                   nonconfig=False, boolean=False,
                   accumulate=False, default=None,
                   help_str=None, help_cookie=None,
                   override=False):
        """Define an option.

        Args:
            internal_name (str): Internal name of the option.
                Use '_' to separate words, and the command-line parser will
                allow the hyphen '-' to be used interchangeably.
            group (str or set of str, optional): Group or set of groups this
                option belongs to. Can be None (in which case the option is
                assumed to always be accepted).
            cmdline (str, optional): Long command-line flag for the option.
                Can be used in addition to the internal name of the option.
            cmdline_short (str, optional): Short command-line flag for the option.
            env (str, optional): Environment variable name for the option.
            cgi (str, optional): CGI argument name for the option.
            config (str, optional): Configuration item name for the option.
                Within a config file, internal names can also be used directly.
            nonconfig (bool, optional): This option may not be set
                by internal name from a configuration file. Defaults to False.
            boolean (bool, optional): Option is a boolean flag.
                If true and cmdline is defined, will also define a negated
                version of the flag (e.g. '--default-repo'
                and '--no-default-repo').
            default (optional): Default value for the option.
            help_str (str, optional): A description of the option.
            help_cookie (str, optional): A 'placeholder' string for
                the option value to be used in the help message.
            override (bool, optional): This option is known to override
                a prior option. If False, will print a warning when overriding.
        """

        groups = []
        if isinstance(group, str):
            groups.append(group)
        elif isinstance(group, set):
            groups += list(group)
        elif group is None:
            groups.append('this_command')
        for group_name in groups:
            if group_name not in cls._option_groups:
                cls._option_groups[group_name] = set()
            cls._option_groups[group_name].add(internal_name)

        if internal_name in cls._options and not override:
            warn_print("overriding definition for option '{}'" \
                    .format(internal_name))
        cls._options.add(internal_name)
        if boolean and default is None:
            default = False
        cls._option_info[internal_name, 'default_value'] = default

        cls._option_info[internal_name, 'help_str'] = help_str
        cls._option_info[internal_name, 'help_cookie'] = help_cookie

        cls._add_option_name('cmdline', cmdline, internal_name, override)
        assert(cmdline_short is None or len(cmdline_short) == 1) # short option must be 1char
        cls._add_option_name('cmdline_short', cmdline_short, internal_name, override)
        cls._add_option_name('env', env, internal_name, override)
        cls._add_option_name('cgi', cgi, internal_name, override)
        cls._add_option_name('config', config, internal_name, override)

        cls._option_info[internal_name, 'nonconfig'] = nonconfig
        cls._option_info[internal_name, 'boolean_flag'] = boolean
        cls._option_info[internal_name, 'accumulate'] = accumulate

        # Also generate a negating version for boolean command line options:
        if cmdline is not None and boolean:
            cls._negated_options['no'+internal_name] = internal_name
            cls._negated_options['no-'+internal_name] = internal_name

    # <TODO>: add_legacy_options to wrap from_cmdline?

    def __init__(self, bunsen, script_name=None, usage_str=None, expected_groups=set()):
        """Initialize a BunsenOptions object representing a command.

        Args:
            bunsen (Bunsen): Bunsen repo this command will run against.
            script_name (str, optional): Name of command or analysis script.
            usage_str (str, optional): Help string for the command or analysis script.
            expected_groups (set, optional): Options groups that will be
              used by this command or analysis script.
        """
        self._bunsen = bunsen
        self.script_name = script_name
        self.usage_str = usage_str
        self.expected_groups = expected_groups
        self.expected_groups.update({'bunsen', 'output'})

        # <TODO>: Add a parameter for this.
        self.usage_str = None

        # Set defaults:
        self.sources = {}
        for k, value in self._option_info.items():
            key, attr_or_flag_name = k
            if attr_or_flag_name != 'default_value': continue
            self.set_option(key, value, 'default')

        # Set additional computed defaults:
        self.set_option('bunsen_default_repo', str(Path.home() / ".bunsen"), 'default')

        # Save configuration files & args in case later sections are activated:
        self._delayed_config = []
        self._unknown_args = []

        # Handling required and optional positional args:
        self._positional_args = []
        self._required_args = [] # XXX set by parse_cmdline, check by check_required
        self._cmdline_required_args = [] # XXX set by parse_cmdline, used by print_help
        self._cmdline_optional_args = [] # XXX set by parse_cmdline, used by print_help

    source_priorities = ['args', 'cgi', 'env', 'local', 'global', 'default']
    """Possible sources of options in order of decreasing priority.

    Here:
    - 'args','cgi' represents command line or CGI arguments
    - 'environment' represents environment variables
    - 'local' represents a local configuration file (in a Bunsen repo)
    - 'global' represents a global configuration file (in user's home directory)
    """

    @classmethod
    def source_overrides(cls, source1, source2):
        """Return True if source1 takes priority over source2.

        If source1 is not present in source_priorities and source2 is present,
        the answer is assumed to be False.
        """
        if source1 in cls.source_priorities:
            source1_ix = cls.source_priorities.index(source1)
        else:
            source1_ix = len(cls.source_priorities)
        if source2 in cls.source_priorities:
            source2_ix = cls.source_priorities.index(source2)
        else:
            source2_ix = len(cls.source_priorities)
        return source1_ix <= source2_ix

    # XXX: Could use in Bunsen.__init__ but the existing (natural)
    # code which directly assigns fields will correctly lead to
    # assigned values treated as being from a lowest-priority
    # (unknown) source.
    def set_option(self, key, value, source, allow_unknown=False):
        """Set an option if it wasn't set from any higher-priority source.

        Args:
            key (str): Internal name of the option.
            value (str): New value for the option.
            source (str): The configuration source of the new value.
                Should be an element of BunsenOptions.source_priorities.
            allow_unknown (bool): Allow setting an option that has
                not been defined. Default False."""
        # <TODO> Warn about duplicate options added from the same source?
        if key in _options_base_fields:
            warn_print("attempt to set reserved BunsenOptions field '{}'" \
                    .format(key))
            return
        if key in self.__dict__ and key in self.sources \
           and not BunsenOptions.source_overrides(source, self.sources[key]):
            return # XXX A value exists with higher priority.
        if key not in self._options:
            if source == 'global' or source == 'local':
                # XXX while cmdline_args() is still used, option may not yet be configured
                self.__dict__[key] = value
                self.sources[key] = source
                return
            if source == 'args' and not allow_unknown:
                err_print("unknown option '{}={}'".format(key, value))
                self.print_help()
                exit(1)
            if not allow_unknown:
                # <TODO> name the config file where this option originated from
                warn_print("unknown option '{}={}', skipping".format(key, value))
                return
            self.__dict__[key] = value
            self.sources[key] = source
            return
        if self.option_info(key, 'boolean_flag') or \
           isinstance(self.option_info(key, 'default_value'), bool):
            if value in {'True','true','yes'}:
                value = True
            elif value in {'False','false','no'}:
                value = False
            elif value == 'html' and key == 'pretty':
                value = 'html' # XXX special-case for option pretty=yes|no|html
            elif not isinstance(value, bool):
                warn_print("unknown boolean option '{}={}'".format(key, value))
                return # keep the default value
        elif isinstance(self.option_info(key, 'default_value'), int):
            if value in {'infinity', 'unlimited'}:
                value = -1
            value = int(value)
        self.__dict__[key] = value
        self.sources[key] = source
        # <TODO>: Check for any _delayed_config sections that were activated?

    def add_config(self, section, config=None, is_global=False, is_project=False):
        """Add configuration options from an additional config section.

        Args:
            section (str): Name of the config section to add.
            config (ConfigParser): ConfigParser object representing
               the config file to add options from. If None, will scan
               previously seen config files.
            is_global (bool): If True, the config file provided in the
                config argument is a system-wide config
                file. Prioritize the options lower than options from a
                repo-specific config.
            is_project (bool): If True, the name of the config section
                identifies a project. The config section that will be
                parsed is [project "<section>"].

        """
        if config is None:
            # add sections from all active config files
            for config, kind in self._delayed_config:
                is_global = kind == 'global'
                self.add_config(section, config,
                                is_global=is_global, is_project=is_project)
            return
        if is_project:
            section = 'project "{}"'.format(section)
        if section not in config:
            return
        for key, value in config[section].items():
            if self.option_name(key, 'config') is not None:
                self.set_option(self.option_name(key, 'config'),
                                value, 'global' if is_global else 'local')
            elif not self.option_info(key, 'nonconfig'):
                self.set_option(key, value,
                                'global' if is_global else 'local')
            else:
                warn_print("attempt to set non-config option '{}' from config" \
                           .format(key))
                pass # don't set anything

    def parse_config(self, config_path, global_config=False):
        """Parse a config file in INI format.

        Args:
            config_path (Path or str): Path to config file.
            global_config (bool, optional): Config file is global
                (and should have lower priority than local config files).

        Returns:
            self
        """
        config = ConfigParser()
        if Path(config_path).is_file():
            config.read(str(config_path))
        else:
            raise BunsenError("configuration file {} not found".format(config_path))

        # section [core], [<script_name>]
        self.add_config('core', config, global_config)
        if self.script_name is not None:
            self.add_config(self.script_name, config, global_config)

        # section [project "<project>"]
        # XXX Load only when the project is unambigous:
        projects = self.get_list('project')
        if projects is not None and len(projects) == 1:
            self.add_config(projects[0], config, global_config, is_project=True)

        # <TODOXXX> handle sections [bunsen-{add,push} {,"<project>"}] -> in bunsen-add, bunsen-upload.py implementations

        # Save config in case other sections are requested later.
        self._delayed_config.append \
            ((config, 'global' if global_config else 'local'))

    def parse_environment(self, env):
        """Parse a set of environment variables.

        Args:
            env (map or environ): Environment variables.

        Returns:
            self
        """
        for external_name, internal_name in self.option_names('env'):
            if external_name in env:
                self.set_option(internal_name, env[external_name], 'env')
            if internal_name in env:
                self.set_option(internal_name, env[internal_name], 'env')
        return self

    def _cmdline_err(self, msg):
        err_print(msg)
        print()
        self.print_help()
        exit(1)

    # TODOXXX update to self. API changes
    def _proc_cmdline_arg(self, arg, next_arg, allow_unknown,
                          accumulate_front=None):
        #print("DEBUG proc_cmdline_arg={},{} allow_unknown={}".format(arg, next_arg, allow_unknown))
        m = cmdline_arg_regex.fullmatch(arg) # XXX always matches
        internal_name, use_next = None, False
        flag, val = None, None
        is_negating = False
        if len(arg) >= 2 and arg.startswith('-') and not arg.startswith('--'):
            # handle '-o arg', '-oarg'
            flag = arg[1:2]
            val = arg[2:]
            if len(val) == 0:
                val = None

            if self.option_name(flag, 'cmdline_short') is not None:
                internal_name = self.option_name(flag, 'cmdline_short')
            elif allow_unknown:
                self._unknown_args.append(arg)
                return use_next
            else:
                self._cmdline_err("unknown flag '{}'".format(arg)) # TODOXXX Delay towards the end.

            next_m = None
            if val is None and next_arg is not None:
                next_m = cmdline_arg_regex.fullmatch(next_arg) # XXX always matches

            if val is None and self.option_info(internal_name, 'boolean_flag'):
                val = True
            elif val is None and next_m is not None and \
                 next_m.group('prefix') is not None:
                self._cmdline_err("option '{}' expects an argument" \
                    .format(arg)) # TODOXXX Delay towards the end.
            elif val is None and next_arg is not None:
                val, use_next = next_arg, True
            elif val is None:
                self._cmdline_err("option '{}' expects an argument" \
                    .format(arg)) # TODOXXX Delay towards the end.
        elif arg.startswith('+'):
            if self.script_name is not None:
                # <TODO>: Support specifying a script for some subcommands e.g. 'add'.
                # <TODO>: Support chained scripts; for now, signal error.
                self._cmdline_err("redundant script specifier '{}'" \
                    .format(arg)) # TODOXXX Delay towards the end.
            # XXX: The '+' will be stripped by Bunsen.find_script().
            self.script_name = arg
            return use_next
        elif m.group('keyword') is not None:
            # handle '--keyword=arg', 'keyword=arg'
            flag = m.group('keyword')
            val = m.group('arg')
            # PR25090: Allow e.g. +source-repo= instead of +source_repo=
            flag = flag.replace('-','_')
            if self.option_name(flag, 'cmdline') is not None:
                internal_name = self.option_name(flag, 'cmdline')
            elif flag in self._negated_options:
                internal_name = self._negated_options[flag]
                is_negating = True
            elif flag in self._options:
                internal_name = flag
            elif allow_unknown:
                # XXX Set the option, for scripts like bunsen-add
                # that take arbitrary options to set testrun metadata fields.
                self.set_option(flag, val, 'args', allow_unknown=True)
                self._unknown_args.append(arg)
                return use_next
            if is_negating: val = self._negate_arg(val) # TODOXXX also negates str
        elif m.group('prefix') is not None:
            # handle '--keyword', '--keyword arg'
            flag = m.group('arg')
            # PR25090: Allow e.g. +source-repo= instead of +source_repo=
            flag = flag.replace('-','_')
            if self.option_name(flag, 'cmdline') is not None:
                internal_name = self.option_name(flag, 'cmdline')
            elif flag in self._negated_options:
                internal_name = self._negated_options[flag]
                is_negating = True
            elif flag in self._options:
                internal_name = flag
            elif allow_unknown:
                # XXX Don't set option if it was given in GNU format,
                # we don't know if it takes an arg or not.
                self._unknown_args.append(arg)
                return use_next
            else:
                self._cmdline_err("unknown flag '{}'" \
                    .format(flag)) # TODOXXX Delay towards the end.

            if next_arg is not None:
                next_m = cmdline_arg_regex.fullmatch(next_arg) # XXX always matches
            has_next_arg = next_arg is not None and next_m.group('prefix') is None
            if self.option_info(internal_name, 'boolean_flag'):
                # XXX Don't support '--opt-foo yes' in command line args,
                # only 'opt-foo=yes', '--opt-foo=yes',
                # and '--opt-foo/--no-opt-foo'.
                val = True
            elif not has_next_arg:
                self._cmdline_err("option '{}' expects an argument" \
                    .format(flag)) # TODOXXX Delay towards the end.
            else:
                val, use_next = next_arg, True
            if is_negating: val = self._negate_arg(val)
        else:
            self._positional_args.append(arg)
            return use_next

        if internal_name is None:
            err_print("unknown option '{}'".format(arg))
            self.print_help() # <TODOXXX> Delay this towards the end, here and elsewhere.
            exit(1)
        elif internal_name in self._options \
           and self.option_info(internal_name, 'accumulate'):
            self._append_option(internal_name, val, 'args', accumulate_front) # <TODOXXX>
        else:
            self.set_option(internal_name, val, 'args')
        return use_next

    def parse_cmdline(self, args, required_args=[], optional_args=[],
                      is_sys_argv=True, allow_unknown=False):
        """Parse a set of command line arguments.

        Args:
            args (list of str): Command line arguments.
            required_args (list of str): Internal names of required arguments.
                These must be specified on the command line if they were not
                specified by another configuration source.
            optional_args (list of str): Internal names of arguments that
                can be specified as positional arguments
                (after the required arguments).
            is_sys_argv (bool, optional): Command line arguments are from sys.argv.
                The first argument (the name of the program) will be skipped.
                Defaults to True.
            allow_unknown (bool, optional): Avoid signaling an error on
                encountering unknown command line arguments.
                Defaults to False.

        Returns:
            self
        """
        self._cmdline_required_args = required_args
        self._cmdline_optional_args = optional_args

        # Handle sys.argv[0]:
        if len(args) > 0 and is_sys_argv:
            args = args[1:]

        # Handle keyword args and flags:
        i = 0
        accumulate_front = {} # XXX args shouldn't accumulate in reverse order
        while i < len(args):
            arg, next_arg = args[i], None
            if i + 1 < len(args):
                next_arg = args[i+1]
            use_next = self._proc_cmdline_arg(arg, next_arg, allow_unknown,
                                              accumulate_front)
            i += 2 if use_next else 1

        # Handle positional args:
        check_required = False # will check for required_args not in _positional_args
        j = 0 # index into self._positional_args
        for i in range(len(required_args)):
            if j >= len(self._positional_args):
                check_required = True
                break
            if required_args[i] in self.__dict__ \
               and self.sources[required_args[i]] == 'args':
                continue # argument is already specified
            self.set_option(required_args[i], self._positional_args[j], 'args')
            j += 1
        for i in range(len(optional_args)):
            if j >= len(self._positional_args):
                break
            if optional_args[i] in self.__dict__ \
               and self.sources[optional_args[i]] == 'args':
                continue # argument is already specified
            self.set_option(optional_args[i], self._positional_args[j], 'args')
        if j < len(self._positional_args):
            warn_print("unexpected extra positional argument '{}'" \
                       .format(self._positional_args[j]))
            self.print_help()
            exit(1)

        # Handle accumulating parameters:
        for key, val in accumulate_front.items():
            if isinstance(self.__dict__[key], list):
                self.__dict__[key] = [val] + self.__dict__[key]
            elif isinstance(self.__dict__[key], str):
                self.__dict__[key] = val + "," + self.__dict__[key]
            else:
                self.__dict__[key] = val + "," + str(self.__dict__[key])

        # Saved required args for later verification:
        if check_required:
            self._required_args = required_args

        return self

    def parse_cgi_query(self, form):
        """Parse a set of CGI options.

        Args:
            form (cgi.FieldStorage): CGI options.

        Returns:
            self
        """
        for external_name, internal_name in self.option_names('cgi'):
            if external_name in form:
                self.set_option(internal_name, form[external_name], 'cgi')
            if internal_name in form:
                self.set_option(internal_name, form[internal_name], 'cgi')
        return self

    def check_required(self):
        """Check if required arguments from a previous parse_cmdline() invocation
        were set from any configuration source. If not, output an error
        message and set self.should_print_help."""
        for internal_name in self._required_args:
            if internal_name not in self.__dict__ \
               or self.sources[internal_name] == 'default':
                # TODO: Should use cmdline name instead?
                err_print("missing required argument '{}'".format(internal_name))
                self.should_print_help = True

    # TODOXXX: Legacy method from Bunsen class, merge into print_help():
    def _print_usage(self, info, args, usage=None,
                     required_args=[], optional_args=[]):
        if usage is not None:
            warn_print("USAGE:", usage, prefix="")
            return

        LINE_WIDTH = 80
        usage, arg_info, offset = "", "", 0
        usage += "USAGE: "
        offset += len("USAGE: ")
        usage += "+" + self.script_name
        offset += len("+" + self.script_name)
        indent = " " * (offset+1) # TODO: Needs tweaking for long script names.

        arginfo_map = {}
        required_arginfo = []
        optional_arginfo = []
        other_arginfo = []
        for t in args:
            # TODO: Later args could override earlier args with same name.
            name, default_val, cookie, description = t
            internal_name = self.option_name(name, 'cmdline')
            if internal_name is None: internal_name = name # XXX
            arginfo_map[internal_name] = t
            if internal_name not in required_args and internal_name not in optional_args:
                other_arginfo.append(t)
        for internal_name in required_args:
            required_arginfo.append(arginfo_map[internal_name])
        for internal_name in optional_args:
            optional_arginfo.append(arginfo_map[internal_name])
        all_args = required_arginfo + optional_arginfo + other_arginfo
        for t in all_args:
            name, default_val, cookie, description = t
            if cookie is None and name == 'pretty' \
                 and isinstance(default_val, bool):
                cookie = 'yes|no|html' if default_val else 'no|yes|html'
            elif cookie is None and isinstance(default_val, bool):
                cookie = 'yes|no' if default_val else 'no|yes'
            elif cookie is None and isinstance(default_val, int):
                # XXX: Need to sort *after* bool since a bool is also an int :/
                cookie = "<num>"

            basic_arg_desc = "{}={}".format(name, cookie)
            internal_name = self.option_name(name, 'cmdline')
            if internal_name is None: internal_name = name
            if internal_name in required_args:
                arg_desc = "[{}=]{}".format(name, cookie)
            elif internal_name in optional_args:
                arg_desc = "[[{}=]{}]".format(name, cookie)
            else:
                arg_desc = "[{}={}]".format(name, cookie)
            arg_width = 1 + len(arg_desc) # XXX includes a space before
            if offset + arg_width >= LINE_WIDTH:
                usage += "\n" + indent
                offset = len(indent) + arg_width
            else:
                usage += " "
                offset += arg_width
            usage += arg_desc

            # TODO: adjust \t to width of arg names?
            arg_info += "- {}\t{}\n".format(basic_arg_desc, description)
        if info is not None:
            usage += "\n\n"
            usage += info
        usage += "\n\nArguments:\n"
        usage += arg_info
        warn_print(usage, prefix="")

    # TODO Should sort command line args further by group.
    # TODOXXX Docstring. For now, imitate prior print_usage.
    def print_help(self):
        args = []
        # TODO: Need access to the script info message here. self._opts.info?
        # TODO: Sort arguments in the correct order.
        # TODO: For the baseline Bunsen command, print these groups:
        skipped_groups = {'bunsen', 'init', 'commit', 'run'}
        skipped_opts = set()
        for group in skipped_groups:
            for internal_name in self._option_groups[group]:
                skipped_opts.add(internal_name)
        cmdline_opts = set()
        for external_name, internal_name in self.option_names('cmdline'):
            if internal_name in skipped_opts:
                continue
            cmdline_opts.add(internal_name)
            default_val = self.option_info(internal_name, 'default_value')
            cookie = self.option_info(internal_name, 'help_cookie')
            description = self.option_info(internal_name, 'help_str')
            args.append((external_name, default_val, cookie, description))
        for internal_name in self._options:
            if internal_name in skipped_opts:
                continue
            if internal_name in cmdline_opts:
                continue
            default_val = self.option_info(internal_name, 'default_value')
            cookie = self.option_info(internal_name, 'help_cookie')
            description = self.option_info(internal_name, 'help_str')
            args.append((internal_name, default_val, cookie, description))
        self._print_usage(self.usage_str, args,
                          required_args=self._cmdline_required_args,
                          optional_args=self._cmdline_optional_args)

    # TODO fields and methods for a testruns/testlogs query
    # -- if the Git commands are any guide, queries can get very complex
    # but all the particular script might care about is 'list of testruns'

    # TODO fields and methods for proper output in different formats
    # -- can take a formatter object from format_output.py?
    # -- or should we import format_output.py directly?

    def _split_list(self, value):
        # TODO: Handle quoted/escaped commas.
        items = []
        for val in value.split(","):
            if val == "": continue
            items.append(val.strip())
        return items

    def get_list(self, key, default=None):
        """Parse an option that was specified as a comma-separated list."""
        if key not in self.__dict__ or self.__dict__[key] is None:
            return default
        if isinstance(self.__dict__[key], list): # XXX already parsed
            return self.__dict__[key]
        return self._split_list(self.__dict__[key])

    # TODO logic for checking cgi_safe functionality

    def _show_results(self):
        """For debugging: print the contents of this BunsenOptions object."""
        print("OBTAINED OPTIONS")
        print ("script_name = {}".format(self.script_name))
        print ("expected_groups = {}".format(self.expected_groups))
        # TODO: Filter by active options groups?
        for key, val in self.__dict__.items():
            if key in _options_base_fields:
                continue
            print ("{} = {} ({})".format(key, val, self.sources[key]))
        if len(self._unknown_args) > 0:
            print ("_unknown_args = {}".format(self._unknown_args))
        # TODOXXX Print delayed_config?
        print()

    def script_env(self):
        """Output environment variable options as a dict."""
        env_values = {}
        for key, internal_name in self.option_names('env'):
            env_values[key] = str(self.__dict__[internal_name])
        return env_values

    def script_args(self):
        """Output non-default options as a list of command line args.

        Omits any options that can be specified via script variables.
        """
        env_keys = set()
        arg_values = []
        for key, internal_name in self.options_names('env'):
            # Skip values that can be satisfied by script_env:
            env_keys.add(internal_name)
        for key, val in self.__dict__.items():
            if key in _options_base_fields:
                continue
            if self.source[key] == 'default':
                # XXX Assume Bunsen child process will have the same defaults.
                continue
            if key in env_keys:
                continue
            if isinstance(val, list):
                # TODO: Escape commas in val.
                val = ",".join(val)
            # TODO: Handle other arg types as necessary.
            arg_values.append("{}={}".format(key, str(val)))
        arg_values += self.unknown_args
        return arg_values

    # TODO script_env, script_args for script invocation

# Options for bunsen:
BunsenOptions.add_option('bunsen_dir', group='bunsen',
    cmdline='repo', env='BUNSEN_DIR', nonconfig=True, default=None,
    help_str="Path to Bunsen repo.")
BunsenOptions.add_option('bunsen_git_repo', group='bunsen',
    cmdline='git-repo', env='BUNSEN_GIT_REPO', default=None,
    help_str="Path to Bunsen repo; default '$(bunsen_dir)/bunsen.git'.")
BunsenOptions.add_option('bunsen_dir_name', group='bunsen',
    default=None,
    help_str="Name of Bunsen repo to search for; default '.bunsen'.")
BunsenOptions.add_option('bunsen_default_repo', group='bunsen',
    default=None, # XXX default=$HOME/.bunsen
    help_str="Path to the default Bunsen repo.")
BunsenOptions.add_option('use_bunsen_default_repo', group='bunsen',
    cmdline='default-repo', boolean=True,
    help_str="Use the default Bunsen repo if a Bunsen repo is not found.")
  # TODO: Fallback if config, required if command line arg?
BunsenOptions.add_option('config_path', group='bunsen',
    cmdline='config', nonconfig=True, default=None,
    help_str="Path to config file; default '$(bunsen_dir)/config'.")
# TODO: Add a config option to use a different repo than bunsen.git?

# Options for {init,commit}:
BunsenOptions.add_option('git_user_name', group={'init','commit'},
    cmdline='git-user-name', cgi='git_user_name', default='bunsen',
    help_str="Username to use for Git commits to the Bunsen repo.")
BunsenOptions.add_option('git_user_email', group={'init', 'commit'},
    cmdline='git-user-email', cgi='git_user_email', default='unknown@email',
    help_str="Email to use for Git commits to the Bunsen repo.")
# XXX: These git options are set for working directory clones, not for
# the main Bunsen git repo. Changing them does not affect an already cloned
# working directory.

# TODOXXX extract script_name whenever group 'run' is configured?
# TODO add these options to bunsen add, bunsen run?
# Options for {run}:
BunsenOptions.add_option('scripts_search_path', group={'run'},
    cmdline='scripts-search-path', cmdline_short='I', default=None,
    accumulate=True,
    help_str="Additional directories to search for analysis scripts.")
# TODOXXX add --script-name command line option as alternative to +script?

# Options for output:
# TODOXXX output_format=json,html,console,log
# TODOXXX -o output_dest.html, hint message "Output written to <path>"

# Options for ???:
# TODOXXX name of bunsen branch to check out?

# TODOXXX add --help command line option
BunsenOptions.add_option('should_print_help', group=None,
    cmdline='help', nonconfig=True, boolean=True, default=False,
    help_str="Show this help message")
