import configparser
import logging
import time
import feedparser
import threading
import sqlite3

from mpd import MPDClient, MPDError, CommandError, ConnectionError

import aiy.audio
import aiy.voicehat

class PodCatcher(threading.Thread):
    def __init__(self, configpath):
        """ Define variables used by object
        """
        threading.Thread.__init__(self)
        self.configPath = configpath

    def _connectDB(self):
        try:
            conn = sqlite3.connect('/tmp/podcasts.sqlite')
            conn.cursor().execute('''
                CREATE TABLE IF NOT EXISTS podcasts (
                    podcast TEXT NOT NULL,
                    title TEXT NOT NULL,
                    ep_title TEXT NOT NULL,
                    url TEXT UNIQUE NOT NULL,
                    timestamp INT NOT NULL);''')
            return conn

        except Error as e:
            print(e)

        return None

    def syncPodcasts(self, filter=None):
        config = configparser.ConfigParser()
        config.read(self.configPath)
        podcasts = config['podcasts']

        conn = self._connectDB()
        cursor = conn.cursor()

        logging.info('Start updating podcast data')
        for podcast,url in podcasts.items():
            if filter is not None and not podcast == filter:
                continue

            logging.info('loading ' + podcast + ' podcast feed')

            rss = feedparser.parse(url)

            # get the total number of entries returned
            resCount = len(rss.entries)
            logging.info('feed contains ' + str(resCount) + ' items')

            # exit out if empty
            if not resCount > 0:
                logging.warning(podcast + ' podcast feed is empty')
                continue

            for rssItem in rss.entries:
                result = {
                    'podcast':podcast,
                    'url':None,
                    'title':None,
                    'ep_title':None,
                    'timestamp':0
                }

                if 'title' in rss.feed:
                    result['title'] = rss.feed.title

                # Abstract information about requested item

                if 'title' in rssItem:
                    result['ep_title'] = rssItem.title

                if 'published_parsed' in rssItem:
                    result['timestamp'] = time.mktime(rssItem['published_parsed'])

                if 'enclosures' in rssItem and len(rssItem.enclosures) > 0:
                    result['url'] = rssItem.enclosures[0]['href']

                elif 'media_content' in rssItem and len(rssItem.media_content) > 0:
                    result['url'] = rssItem.media_content[0]['url']

                else:
                    logging.warning('The feed for "' + podcast + '" is in an unknown format')
                    continue

                cursor.execute('''REPLACE INTO podcasts(podcast, title, ep_title, url, timestamp)
                    VALUES(?, ?, ?, ?, ?)''', (result['podcast'], result['title'], result['ep_title'], result['url'], result['timestamp']))

        conn.commit()
        logging.info('Finished updating podcast data')

    def getPodcastInfo(self, podcast=None, offset=0):
        if podcast is None:
            return None

        logging.info('Searching for information about "' + str(podcast) + '" podcast')
        conn = self._connectDB()
        cursor = conn.cursor()
        cursor.execute("SELECT url, title, ep_title, (strftime('%s','now') - strftime('%s', datetime(timestamp, 'unixepoch', 'localtime')))/3600  as age FROM podcasts WHERE podcast LIKE ? ORDER BY timestamp DESC LIMIT ?,1", (podcast,offset,))
        result = cursor.fetchone()
        if (result):
            return {
                'url':result[0],
                'title':result[1],
                'ep_title':result[2],
                'age':result[3]
            }

        return None

    def run(self):
        while True:
            self.syncPodcasts()
            time.sleep(1680)

class Music(object):

    """Interacts with MPD"""

    def __init__(self, configpath):
        self._cancelAction = False
        self.configPath = configpath
        self._confirmPlayback = False
        self._podcastURL = None
        self.mpd = MPDClient(use_unicode=True)

    def command(self, module, voice_command, podcatcher=None):
        self.resetVariables()
        self.mpd.connect("localhost", 6600)

        if module == 'music':
            if voice_command == 'stop':
                self.mpd.stop()
                self.mpd.clear()

            elif voice_command == 'resume' or voice_command == 'play':
                self.mpd.pause(0)

            elif voice_command == 'pause':
                self.mpd.pause(1)

        elif module == 'radio':
            self.playRadio(voice_command)

        elif module == 'podcast':
            self.playPodcast(voice_command, podcatcher)

        if self._cancelAction == False:
            time.sleep(1)
            button = aiy.voicehat.get_button()
            button.on_press(self._buttonPressCancel)

            # Keep alive until the user cancels music with button press
            while self.mpd.status()['state'] != "stop":
                if self._cancelAction == True:
                    logging.info('stopping Music by button press')
                    self.mpd.stop()
                    self._podcastURL = None
                    break

                time.sleep(0.1)
            button.on_press(None)
            logging.info('Music stopped playing')
            self.mpd.clear()

        try:
            self.mpd.close()
            self.mpd.disconnect()
        except ConnectionError:
            logging.warning('MPD connection timed out')
            pass

    def playRadio(self, station):
        config = configparser.ConfigParser()
        config.read(self.configPath)

        stations = config['radio']

        if station == 'list':
            logging.info('Enumerating radio stations')
            aiy.audio.say('Available stations are')
            for key in stations:
                aiy.audio.say(key)
            return

        elif station not in stations:
            logging.info('Station not found: ' + station)
            aiy.audio.say('radio station ' + station + ' not found')
            return

        logging.info('streaming ' + station)
        aiy.audio.say('tuning the radio to ' + station)

        self._cancelAction = False

        self.mpd.clear()
        self.mpd.add(stations[station])
        self.mpd.play()

    def playPodcast(self, podcast, podcatcher=None):
        config = configparser.ConfigParser()
        config.read(self.configPath)
        podcasts = config['podcasts']
        logging.info('playPodcast "' + podcast + "'")

        offset = 0
        if podcatcher is None:
            logging.warning('playPodcast missing podcatcher object')
            return

        if self._confirmPlayback == True:
            self._confirmPlayback = False

        else:
            if podcast == 'list':
                logging.info('Enumerating Podcasts')
                aiy.audio.say('Available podcasts are')
                for key in podcasts:
                    aiy.audio.say('' + key)
                return

            elif podcast == 'recent':
                aiy.audio.say('Recent podcasts are')
                for title,url in podcasts.items():
                    podcastInfo = podcatcher.getPodcastInfo(title, offset)
                    if podcastInfo is None:
                        continue
                    elif podcastInfo['age'] < 24:
                        aiy.audio.say('' + podcastInfo['title'] + ' uploaded an episode ' + str(int(podcastInfo['age'])) + ' hours ago')
                    else:
                        aiy.audio.say('' + podcastInfo['title'] + ' uploaded an episode ' + str(int(podcastInfo['age']/24)) + ' days ago')
                return

            elif podcast == 'today':
                aiy.audio.say('Today\'s podcasts are')
                for title,url in podcasts.items():
                    podcastInfo = podcatcher.getPodcastInfo(title, offset)
                    if podcastInfo is not None and podcastInfo['age'] < 24:
                        aiy.audio.say('' + title + ' uploaded an episode ' + str(int(podcastInfo['age'])) + ' hours ago')
                self._cancelAction = True
                return

            elif podcast == 'yesterday':
                aiy.audio.say('Yesterday\'s podcasts are')
                for title,url in podcasts.items():
                    podcastInfo = podcatcher.getPodcastInfo(title, offset)
                    if podcastInfo is not None and podcastInfo['age'] < 48 and podcastInfo['age'] > 24:
                        aiy.audio.say('' + title + ' uploaded an episode ' + str(int(podcastInfo['age'])) + ' hours ago')
                return

            elif podcast.startswith('previous '):
                offset = 1
                podcast = podcast[9:]

            if podcast not in podcasts:
                logging.info('Podcast not found: ' + podcast)
                aiy.audio.say('Podcast ' + podcast + ' not found')
                return

            podcastInfo = podcatcher.getPodcastInfo(podcast, offset)
            if podcastInfo == None:
                logging.warning('Podcast data for "' + podcast + '" failed to load')
                return
            logging.info('Podcast Title: ' + podcastInfo['title'])
            logging.info('Episode Title: ' + podcastInfo['ep_title'])
            logging.info('Episode URL: ' + podcastInfo['url'])
            logging.info('Episode Age: ' + str(podcastInfo['age']) + ' hours')

            aiy.audio.say('Playing episode of ' + podcastInfo['title'] + ' titled ' + podcastInfo['ep_title'])

            self._podcastURL = podcastInfo['url']

            if (podcastInfo['age'] > 336):
                aiy.audio.say('This episode is ' + str(int(podcastInfo['age']/24)) + ' days old. Do you still want to play it?')
                self._confirmPlayback = True
                return None

        self._cancelAction = False
        if self._podcastURL is None:
            return None

        try:
            self.mpd.clear()
            self.mpd.add(self._podcastURL)
            self.mpd.play()
        except ConnectionError as e:
            aiy.audio.say('Error connecting to MPD service')

        self._podcastURL = None

    def _buttonPressCancel(self):
        self._cancelAction = True

    def getConfirmPlayback(self):
        return self._confirmPlayback

    def setConfirmPlayback(self, confirmPlayback):
        self._confirmPlayback = confirmPlayback == True

    def getPodcastURL(self):
        return self._podcastURL

    def setPodcastURL(self, podcastURL):
        self._podcastURL = podcastURL

    def resetVariables(self):
        self._cancelAction = False