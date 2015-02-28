#!/usr/bin/env python
"""

A script for making incremental snapshot backups of directories using rsync.
See README.markdown for instructions.

"""
import datetime
import sys
import os
import subprocess
import optparse


class CalledProcessError(Exception):

    """Exception type that's raised if an external command fails."""

    def __init__(self, command, output, exit_value):
        super(CalledProcessError, self).__init__(
            output + " " + str(exit_value))
        self.command = command
        self.output = output
        self.exit_value = exit_value


class NoSuchCommandError(Exception):

    """Raised when trying to run an external command that doesn't exist."""

    def __init__(self, command, message):
        super(NoSuchCommandError, self).__init__(message)
        self.command = command


def _run(command):
    """Run the given command as a subprocess and return its stdout output.

    This redirects the subprocess's stderr to stdout so the returned string
    could contain everything printed to stdout and stderr together.

    :raises CalledProcessError: If running the command fails or the command
        exits with non-zero status. The command's stdout and stderr will be
        availabled as error.output, and its exit status as error.exit_value.

    :raises NoSuchCommandError: If the command doesn't exist.

    """
    try:
        return subprocess.check_output(
            command.split(), stderr=subprocess.STDOUT)
    except subprocess.CalledProcessError as err:
        raise CalledProcessError(
            command, err.output, err.returncode)
    except OSError as err:
        if err.errno == 2:
            raise NoSuchCommandError(command, err.strerror)
        else:
            raise


def _datetime():
    """Return the current datetime as a string.

    We wrap datetime.datetime.now() instead of calling it directly to make
    it easy for tests to patch this funtion.

    """
    return datetime.datetime.now().strftime("%Y-%m-%dT%H_%M_%S")


def _is_remote(src_or_dest):
    """Return True if src_or_dest is a remote path, False otherwise.

    :param src_or_dest: an rsync source or destination argument
        (as you would pass to rsync on the command line)

    """
    # If it has a : before the first / then it's a remote path.
    return ':' in src_or_dest.split('/')[0]


def _parse_rsync_arg(arg):
    """Parse the given rsync SRC or DEST argument.

    Return a tuple containing the user, host and path parts of the argument.

    user    :       The username in a remote path spec.
                    'seanh' in 'seanh@mydomain.org:/path/to/backups'.
                    None if arg is a local path or a remote path without a
                    username.
    host    :       The hostname in a remote path spec.
                    'mydomain.org' in 'seanh@mydomain.org:/path/to/backups'.
                    None if arg is a local path.
    path    :       The path in a local or remote path spec.
                    '/path/to/backups' in the remote path
                    'seanh@mydomain.org:/path/to/backups'.
                    '/media/BACKUP' in the local path '/media/BACKUP'.

    """
    if _is_remote(arg):
        before_first_colon, after_first_colon = arg.split(':', 1)
        if '@' in before_first_colon:
            user = before_first_colon.split('@')[0]
        else:
            user = None
        host = before_first_colon.split('@')[-1]
        path = after_first_colon
    else:
        user = None
        host = None
        path = os.path.abspath(os.path.expanduser(arg))
    return user, host, path


def snapshot(source, dest, debug=False, compress=True, exclude=None):
    # Make sure source ends with / because this affects how rsync behaves.
    if not source.endswith(os.sep):
        source += os.sep

    date = _datetime()

    user, host, snapshots_root = _parse_rsync_arg(dest)

    rsync_options = [
        # Copy recursively and preserve times, permissions, symlinks, etc.
        '--archive',
        '--partial',
        # Keep partially transferred files if the transfer is interrupted.
        '--partial-dir=partially_transferred_files',
        '--one-file-system',  # Don't cross filesystem boundaries.
        '--delete',  # Delete extraneous files from dest dirs.
        '--delete-excluded',  # Also delete excluded files from dest dirs.
        '--itemize-changes',  # Output a change-summary for all updates.
        # Make hard-links to the previous snapshot, if any.
        '--link-dest=../latest.snapshot',
        '--human-readable',  # Output numbers in a human-readable format.
        '--quiet',  # Suppress non-error output messages.
        '--compress',  # Compress files during transfer.
        '--fuzzy',  # Look for basis files for any missing destination files.
        ]

    if os.path.isfile(os.path.expanduser("~/.snapshotter/excludes")):
        # Read exclude patterns from file.
        rsync_options.append('--exclude-from=$HOME/.snapshotter/excludes')
    if debug:
        rsync_options.append('--dry-run')
    if exclude is not None:
        for pattern in exclude:
            rsync_options.append("--exclude '%s'" % pattern)

    rsync_cmd = "rsync %s %s " % (' '.join(rsync_options), source)
    if host is not None:
        if user is not None:
            rsync_cmd += "%s@" % user
        rsync_cmd += "%s:" % host
    rsync_cmd += "%s/incomplete.snapshot" % snapshots_root

    # Construct the `mv && rm && ln` command to be executed after the rsync
    # command completes successfully.
    mv_cmd = ""
    if host is not None:
        mv_cmd += "ssh "
        if user is not None:
            mv_cmd += "%s@" % user
        mv_cmd += '%s "' % host
    mv_cmd += "mv %s/incomplete.snapshot %s/%s.snapshot" % (
        snapshots_root, snapshots_root, date)
    if host is not None:
        mv_cmd += '"'

    rm_cmd = "rm -f %s/latest.snapshot" % snapshots_root
    ln_cmd = "ln -s %s.snapshot %s/latest.snapshot" % (
        date, snapshots_root)

    print(rsync_cmd)
    _run(rsync_cmd)

    if not debug:
        print(mv_cmd)
        _run(mv_cmd)
        print(rm_cmd)
        _run(rm_cmd)
        print(ln_cmd)
        _run(ln_cmd)


class CommandLineArgumentsError(Exception):

    """The exception that's raised if the command-line args are invalid."""

    pass


def _parse_cli(args=None):
    """Parse the command-line arguments."""
    if args is None:
        args = sys.argv[1:]

    parser = optparse.OptionParser(usage="usage: %prog [options] SRC DEST")
    parser.add_option(
        '-d', '--debug', '-n', '--dry-run', dest='debug', action='store_true',
        default=False,
        help="Perform a trial-run with no changes made (pass the --dry-run "
             "option to rsync)")
    parser.add_option(
        '--exclude', type='string', dest='exclude', metavar="PATTERN",
        action='append',
        help="Exclude files matching PATTERN, e.g. --exclude '.git/*' (see "
             "the --exclude option in `man rsync`)")
    (options, args) = parser.parse_args(args)

    if len(args) != 2:
        raise CommandLineArgumentsError(parser.get_usage())

    src = args[0]
    dest = args[1]
    return (src, dest, options.debug, options.exclude)


def main():
    """Parse command-line args and pass them to snapshot().

    Also turns any known exceptions raised into clean sys.exit()s with a
    non-zero exit status and an error message printed, instead of stack traces.

    """
    try:
        src, dest, debug, exclude = _parse_cli()
    except CommandLineArgumentsError as err:
        sys.exit(err.message)
    try:
        snapshot(src, dest, debug, exclude)
    except (CalledProcessError, NoSuchCommandError) as err:
        sys.exit(err.message)


if __name__ == "__main__":
    main()
