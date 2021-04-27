*****************
Mopidy-SoundCloud
*****************

.. image:: https://img.shields.io/pypi/v/Mopidy-SoundCloud
    :target: https://pypi.org/project/Mopidy-SoundCloud/
    :alt: Latest PyPI version

.. image:: https://img.shields.io/github/workflow/status/mopidy/mopidy-soundcloud/CI
    :target: https://github.com/mopidy/mopidy-soundcloud/actions
    :alt: CI build status

.. image:: https://img.shields.io/codecov/c/gh/mopidy/mopidy-soundcloud
    :target: https://codecov.io/gh/mopidy/mopidy-soundcloud
    :alt: Test coverage

`Mopidy <https://mopidy.com/>`_ extension for playing music from
`SoundCloud <https://soundcloud.com>`_.


Description
=================

Branch of the Mopidy-SoundCloud extension, featuring:

- Images for tracks, playlists and albums.
- SoundCloud's Explore functionality.
- HLS audio streaming.


Configuration
=============

#. You must register for a user account at https://soundcloud.com/

#. You need a SoundCloud authentication token for Mopidy from
   https://mopidy.com/authenticate

#. Add the authentication token to the ``mopidy.conf`` config file::

    [soundcloud]
    auth_token = 1-1111-1111111
    explore_songs = 25
    stream_pref = progressive

#. Use ``explore_songs`` to restrict the number of items returned

#. Use ``stream_pref`` to set streaming protocol preference. Possible options
   are ``progressive`` or ``hls``. (``hls`` Streams are more responsive when
   seeking to specific time in track.)


Troubleshooting
===============

If you're having trouble with audio playback from SoundCloud, make sure you
have the "ugly" plugin set from GStreamer installed for MP3 support. The
package is typically named ``gstreamer1.0-plugins-ugly`` or similar, depending
on OS and distribution. The package isn't a strict requirement for Mopidy's
core, so you may be missing it.

If you're using ``hls`` streams, make sure to have the ``gstreamer1.0-plugins-bad``
plugin set from GStreamer installed.


Project resources
=================

- `Source code of master branch <https://github.com/mopidy/mopidy-soundcloud>`_
- `Issue tracker <https://github.com/mopidy/mopidy-soundcloud/issues>`_
- `Changelog <https://github.com/mopidy/mopidy-soundcloud/releases>`_


Credits
=======

- Original author: `Janez Troha <https://github.com/dz0ny>`_
- `Contributors <https://github.com/mopidy/mopidy-soundcloud/graphs/contributors>`_
