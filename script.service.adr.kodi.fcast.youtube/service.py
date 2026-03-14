# pyright: reportDeprecated=false
# pyright: reportUnusedCallResult=false
# pyright: reportImplicitOverride=false
from __future__ import annotations
import socket
import selectors
import struct
import threading
import json
import sys
import time
from typing import Optional, Tuple, List, Dict, Any

import xbmc
import xbmcgui
import xbmcaddon

# defined by the FCast protocol: https://gitlab.com/futo-org/fcast/-/wikis/Protocol-version-1
FCAST_PORT = 46899

# defined in the protocol
FCAST_MAX_BODY_SIZE = 32000 - 1

# 4 byte length + 1 byte opcode
#   - uint32: packet length in bytes, *not* including this field
#   - uint8: opcode
#   - uint8[]: body (may be empty, size=1)
FCAST_HEADER_SIZE = 5

[
    FC_PLAY,
    FC_PAUSE,
    FC_RESUME,
    FC_STOP,
    FC_SEEK,
    FC_PLAYBACKUPDATE,
    FC_VOLUMEUPDATE,
    FC_SETVOLUME,
    FC_PLAYBACKERROR,
    FC_SETSPEED,
    FC_VERSION,
    FC_PING,
    FC_PONG,
] = range(1, 14, 1)

FCAST_HAS_BODY = {
    FC_PLAY: True,
    FC_PAUSE: False,
    FC_RESUME: False,
    FC_STOP: False,
    FC_SEEK: True,
    FC_PLAYBACKUPDATE: True,
    FC_VOLUMEUPDATE: True,
    FC_SETVOLUME: True,
    FC_PLAYBACKERROR: True,
    FC_SETSPEED: True,
    FC_VERSION: True,
    FC_PING: False,
    FC_PONG: False,
}

FCAST_STATE_IDLE, FCAST_STATE_PLAYING, FCAST_STATE_PAUSED = range(3)


# read addon settings
ADDON = xbmcaddon.Addon()


def notification(
    header=ADDON.getAddonInfo("name"),
    message="",
    time=3000,
    icon=ADDON.getAddonInfo("icon"),
    sound=True,
):
    xbmcgui.Dialog().notification(header, message, icon, time, sound)


def log(msg: str, level: int = xbmc.LOGINFO) -> None:
    xbmc.log(f"[FCAST] {msg}", level)


class FCastPlayer(xbmc.Player):
    def __init__(self, session) -> None:
        super().__init__()
        self.session = session

        # basic state management is required
        self._paused = False
        self.prev_time = -1
        self.prev_duration = 1
        self._state = FCAST_STATE_IDLE

    def __get_pb_update(self, has_ended: bool = False) -> dict[str, int | float]:
        # Construct a PlaybackUpdateMessage for the client (GrayJay).
        #
        # This can/should contain (the protocol is unclear):
        #   - generationTime: current unix timestamp in milliseconds
        #   - time: current playing position in seconds
        #   - duration: current media duration in seconds
        #   - state: playback state (0 idle, 1 playing, 2 paused)
        #   - speed: playback speed
        #
        # This is how the typing is set up in GrayJay (the protocol doc
        # doesn't bother to say anything beyond "number" for the numeric fields):
        #       val generationTime: Long
        #       val time: Double
        #       val duration: Double
        #       val state: Int
        #       val speed: Double
        #
        # Excluding duration produces an exception so it's always included.

        pb_update = {"generationTime": int(time.time() * 1000), "speed": 1.0}

        if not has_ended:
            try:
                # valid to call getTotalTime and getTime when something is playing (or paused)
                pb_update["time"] = self.getTime()
                pb_update["duration"] = self.getTotalTime()
                self.prev_duration = pb_update["duration"]
            except Exception as _:
                # if in here we're not playing anything
                self._state = FCAST_STATE_IDLE
                pb_update["time"] = self.prev_duration
                pb_update["duration"] = self.prev_duration
        else:
            log("pb_update: has_ended = True")
            pb_update["time"] = self.prev_duration
            pb_update["duration"] = self.prev_duration

        pb_update["state"] = self._state

        # seems to sometimes return negative values at the start of playback
        if pb_update["time"] < 0:
            pb_update["time"] = 0

        log(f"Generated a pb update: {pb_update}")
        return pb_update

    def handlePlayBackTimeChanged(self, has_ended: bool = False) -> None:
        """
        Send an update to the client when the playback time changes
        """
        pb_update = self.__get_pb_update(has_ended)
        self.prev_time = int(pb_update["time"])

        self.session.send_packet(FC_PLAYBACKUPDATE, pb_update)

    def handleFCastPause(self) -> None:
        log(f"handleFCastPause, self._paused={self._paused}")
        # grayjay seems to send a "PAUSE" after a "STOP", so check if we've already stopped
        # by checking if the state is IDLE
        if not self._paused and self._state != FCAST_STATE_IDLE:
            self.pause()
            self._paused = True
            self._state = FCAST_STATE_PAUSED
            pb_update = self.__get_pb_update()
            self.session.send_packet(FC_PLAYBACKUPDATE, pb_update)

    def handleFCastResume(self) -> None:
        log(f"handleFCastResume, self._paused={self._paused}")
        if self._paused:
            self.pause()
            self._paused = False
            self._state = FCAST_STATE_PLAYING
            pb_update = self.__get_pb_update()
            self.session.send_packet(FC_PLAYBACKUPDATE, pb_update)

    def handleFCastStop(self) -> None:
        log(f"handleFCastStop")
        # Use executebuiltin rather than self.stop() to avoid blocking the FCast
        # message loop while Kodi waits for inputstream.adaptive network timeouts.
        xbmc.executebuiltin("PlayerControl(Stop)")
        self._paused = False
        self._state = FCAST_STATE_IDLE
        pb_update = self.__get_pb_update()
        self.session.send_packet(FC_PLAYBACKUPDATE, pb_update)

    def handleFCastSeek(self, to_pos: float) -> None:
        log(f"handleFCastSeek({to_pos}")
        if self._state != FCAST_STATE_IDLE and self.isPlaying():
            self.seekTime(to_pos)

    ### all methods below are overides of xbmc.Player methods

    def onPlayBackPaused(self) -> None:
        """
        Called when a playing file is paused
        """
        log(f"onPlayBackPause")
        self._paused = True
        self._state = FCAST_STATE_PAUSED

    def onPlayBackResumed(self) -> None:
        """
        Called when the user resumed a paused file
        """
        log(f"onPlayBackResume")
        self._paused = False
        self._state = FCAST_STATE_PLAYING

    def onAVStarted(self) -> None:
        """
        Called when the player actually has a video/audio stream
        """
        log(f"onAVStarted")
        self._state = FCAST_STATE_PLAYING
        self.handlePlayBackTimeChanged()

    def onPlayBackEnded(self) -> None:
        """
        Called when Kodi stops playing a file
        """
        log(f"onPlayBackEnded")
        self._paused = False
        self._state = FCAST_STATE_IDLE
        self.handlePlayBackTimeChanged(has_ended=True)

    def onPlayBackStopped(self) -> None:
        """
        Called when the user stops playback of a file
        """
        log(f"onPlayBackStopped")
        self._paused = False
        self._state = FCAST_STATE_IDLE
        self.handlePlayBackTimeChanged()

    def onPlayBackError(self) -> None:
        """
        Called if playback stops due to an error
        """
        log(f"onPlayBackError")
        self._paused = False
        self._state = FCAST_STATE_IDLE
        self.session.send_packet(
            FC_PLAYBACKERROR, {"message": "An error occurred playing this video"}
        )


class FCastClientSession(threading.Thread):
    def __init__(
        self, connection: socket.socket, address: Tuple[str, int], monitor: xbmc.Monitor
    ) -> None:
        super().__init__()
        self.connection = connection
        self.address = address
        self.monitor = monitor

        self.connection.setblocking(False)
        self.selector = selectors.DefaultSelector()
        _ = self.selector.register(self.connection, selectors.EVENT_READ)
        log(f"Created session for {address}")
        notification(message=f"Connection from {address}")

        self._last_video_id: str | None = None
        self.player = FCastPlayer(self)
        self.active = True

    def close(self) -> None:
        try:
            self.connection.close()
        except Exception as e:
            log(f"Error closing connection in session {e}")
            notification(message=f"Error closing connection in session {e}")

    def __get_fcast_header(self, opcode: int, length: int) -> bytes:
        return struct.pack("<IB", length, opcode)

    def send_packet(
        self, opcode: int, body: Optional[dict[str, int | str | float]] = None
    ) -> None:
        """
        Sends a packet to the client
        """

        # if opcode == FC_PLAYBACKUPDATE:
        if body is not None:
            packet_body = json.dumps(body).encode("utf-8")
        else:
            packet_body = b""

        packet = self.__get_fcast_header(opcode, len(packet_body) + 1)
        packet += packet_body

        try:
            self.connection.send(packet)
        except Exception as e:
            # this happens a lot and doesn't seem to be important???
            # [Errno 9] Bad file descriptor
            if "Bad file descriptor" not in str(e):
                log(f"Error sending to client: {e}", xbmc.LOGERROR)
                notification(message=f"Error sending to client: {e}")
                self.active = False
            else:
                log("Ignoring bad file descriptor error")

    def run(self) -> None:
        # Now we are connected to a client, have to parse and respond to
        # incoming commands until the connection is closed/broken.
        #
        # Unfortunately because the FCAST protocol seems to have been designed to be as
        # barebones as possible, there are no magic bytes to use to pick out packet
        # boundaries from the data stream.

        buffer = b""
        conn_closed = False

        # send a version packet to the client
        self.send_packet(FC_VERSION, {"version": 2})

        while self.active and not self.monitor.abortRequested():
            data: bytes = b""
            events = self.selector.select(0)
            for _, mask in events:
                if mask & selectors.EVENT_READ:
                    try:
                        data = self.connection.recv(4096)
                        if len(data) == 0:  # disconnect
                            log(f"Connection closed for {self.address}")
                            notification(message=f"Disconnect: {self.address}")
                            conn_closed = True
                        # log(f"Received: {len(data)} bytes")
                    except BlockingIOError:
                        pass
                    except Exception as e:
                        notification(message=f"Receive data exception: {e}")
                        log(f"Exception receiving data from client: {e}")
                        conn_closed = True

            if conn_closed:
                break

            # send playback updates every second while playing
            if self.player.isPlaying() and self.player.prev_time != int(
                self.player.getTime()
            ):
                self.player.handlePlayBackTimeChanged()

            # check if we now have enough data to parse a packet. States we can be in:
            #   - buffer is empty, meaning next 5 bytes should be a length + opcode
            #   - buffer is not empty, meaning the last read didn't get everything
            if data:
                buffer += data

            while len(buffer) >= FCAST_HEADER_SIZE:
                length, opcode = struct.unpack("<IB", buffer[:FCAST_HEADER_SIZE])
                if len(buffer) < length + 4:
                    break  # incomplete packet, wait for more data
                try:
                    buffer = self.parse_packet(length, opcode, buffer)
                except Exception as e:
                    log(f"Error parsing packet: {e}", xbmc.LOGERROR)
                    notification(message=f"Client sent bad packet: {e}")
                    self.active = False
                    break

            self.monitor.waitForAbort(0.05)

        self.active = False
        self.selector.unregister(self.connection)
        self.connection.close()

    def parse_packet(self, length: int, opcode: int, buffer: bytes) -> bytes:
        if opcode < FC_PLAY or opcode > FC_PONG:
            log(f"Invalid opcode: {opcode}")
            notification(message="Warning: client sent invalid opcode!")
            raise Exception(f"Invalid opcode: {opcode}")

        if length > FCAST_MAX_BODY_SIZE:
            log(f"Invalid length: {length}")
            notification(message="Warning: client sent invalid packet!")
            raise Exception(f"Invalid packet length: {length}")

        if len(buffer) < length + 4:
            # log(
            #     f"Failed to parse packet because length required is {length+4} and buffer has {len(buffer)} bytes"
            # )
            return buffer

        # some FCast messages are header-only
        if FCAST_HAS_BODY[opcode]:
            body_len = length - 1
            body = buffer[FCAST_HEADER_SIZE : FCAST_HEADER_SIZE + body_len]
            body = json.loads(body.decode("utf-8"))
            self.process_packet(opcode, body)
            buffer = buffer[FCAST_HEADER_SIZE + body_len :]
        else:
            buffer = buffer[FCAST_HEADER_SIZE:]
            self.process_packet(opcode)

        # log(f"Bytes left in buffer = {len(buffer)}")
        return buffer

    def process_packet(
        self, opcode: int, body: Optional[Dict[Any, Any]] = None
    ) -> None:
        log(f"Received opcode: {opcode}")
        if body is not None:
            log(json.dumps(body))

        if opcode == FC_PONG:
            # ignore
            log("Ignoring PONG packet")
        elif opcode == FC_PING:
            _ = self.connection.send(struct.pack("<IB", 1, FC_PONG))
        elif opcode == FC_PLAY and body is not None:
            log("Received a PLAY command!")

            # This is where everything relies on the modified GrayJay app. Normally for YouTube
            # videos, we would get a body containing the DASH manifest in the "content" field.
            # However it seems tricky to get this relayed to InputStream Adaptive without
            # things going wrong.
            #
            # Instead we assume either the "content" or "url" field of the message will be set
            # to a YouTube video ID (just the ID, nothing else), which we can pass on to the 
            # YouTube plugin for playback.
            #
            # Note that the "time" field may be set if GrayJay has previously played the
            # selected video, in which case pass that on too (converting from float to int first).

            video_id = body.get("content") or body.get("url")
            if video_id is None:
                log(f"Failed to extract video ID from message: {body}")
                notification("Failed to extract video ID!")
            else:
                # trigger the youtube plugin to play the selected video
                self.player.play(
                    f"plugin://plugin.video.youtube/play/?video_id={video_id}&seek={int(body.get('time', 0))}"
                )
                self._last_video_id = video_id # save the last received ID here
        elif opcode == FC_PAUSE:
            log("Received a PAUSE command")
            self.player.handleFCastPause()
        elif opcode == FC_RESUME:
            log("Received a RESUME command")
            # when a video reaches the end, GrayJay seems to keep the video "active"
            # while Kodi returns to its "no media" state. In this case, tapping the play
            # button again in GrayJay sends a "resume" command to this plugin, which ends up
            # doing nothing because as far as its concerned there's nothing to resume. So to
            # avoid that, try to detect a resume command arriving while nothing is playing 
            # and if possible restart the last played video
            if not self.player.isPlaying() and self._last_video_id is not None:
                log(
                    f"Found a RESUME command with no loaded video, using previous ID: {self._last_video_id}"
                )
                self.player.play(
                    f"plugin://plugin.video.youtube/play/?video_id={self._last_video_id}&seek={0}"
                )
            else:
                self.player.handleFCastResume()
        elif opcode == FC_STOP:
            log("Received  STOP command")
            self.player.handleFCastStop()
        elif opcode == FC_SEEK and body is not None:
            log(f"Received SEEK command to pos {body['time']}")
            self.player.handleFCastSeek(body["time"])
        elif opcode == FC_VERSION:
            log("Sending VERSION response")
            # the app expects this to be an integer, and will display a compatibility
            # toast if it is <= 1 for some reason
            self.send_packet(FC_VERSION, {"version": 2})
        elif opcode == FC_SETVOLUME and body is not None:
            # I use Yatse to control volume since it supports the hardware buttons
            log(f"Ignoring SETVOLUME: {body['volume']}")
        else:
            log(f"Failed to handle opcode {opcode} with body {body}", xbmc.LOGERROR)
            notification(message=f"Failed to handle opcode {opcode}!")


class FCastServer(threading.Thread):
    def __init__(self, monitor: xbmc.Monitor) -> None:
        super().__init__()
        self.monitor = monitor

        # set up a TCP socket listening on the FCast protocol-defined port
        self.listen_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.listen_sock.settimeout(1)
        self.listen_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

        self.listen_sock.setblocking(False)

        # use the selectors module (wrapping the select() API) to tell us when we have
        # an incoming connection
        self.selector = selectors.DefaultSelector()
        _ = self.selector.register(self.listen_sock, selectors.EVENT_READ, data=None)

        self.sessions: list[FCastClientSession] = []

    def run(self) -> None:
        """Thread function"""

        # TODO should check errors here
        self.listen_sock.bind(("0.0.0.0", FCAST_PORT))
        self.listen_sock.listen(1)
        log("Socket created and listening")

        while not self.monitor.abortRequested():
            # check for connections from an FCast client, without blocking
            events = self.selector.select(timeout=0)

            for _, mask in events:
                if mask & selectors.EVENT_READ:
                    connection, client_addr = self.listen_sock.accept()
                    session = FCastClientSession(connection, client_addr, self.monitor)
                    self.sessions.append(session)
                    session.start()

            self.monitor.waitForAbort(0.5)

            # remove inactive sessions
            inactive = [s for s in self.sessions if not s.active]
            for s in inactive:
                s.close()
            self.sessions = [s for s in self.sessions if s.active]
            # log(f"FCastServer has {len(self.sessions)} active sessions, just removed {len(inactive)} inactive sessions")

        log("FCastServer exiting")
        self.selector.unregister(self.listen_sock)
        self.listen_sock.close()


if __name__ == "__main__":
    # Kodi service addons need to check if the app is exiting (or the user profile is changing).
    # To do this you create a Monitor object and check periodically if an abort has been
    # requested (see https://kodi.wiki/view/Service_add-ons).
    #
    # Since we just wait here on the main thread for any abort requests, the addon
    # needs to spawn at least one other thread to do the actual work.

    log("Starting service " + str(sys.argv))
    monitor = xbmc.Monitor()

    server = FCastServer(monitor)
    server.start()

    while not monitor.abortRequested():
        monitor.waitForAbort(1)

    # TODO: any shutdown tasks
