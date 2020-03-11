# -*- coding: utf-8 -*-
import socket
import random


from BeautifulSoup import BeautifulSoup
from logging.handlers import RotatingFileHandler
import time
import logging
import os
import re
import mechanize
from random import randint
from datetime import datetime
from urllib import urlencode


from selenium.common.exceptions import NoSuchElementException
from selenium.webdriver import ActionChains

from planet import Planet, Moon
from config import options
from selenium import webdriver

from selenium.webdriver.chrome.options import Options
from lxml import etree
import hashlib
from datetime import timedelta


socket.setdefaulttimeout(float(options['general']['timeout']))

#
# COMMANDS
# stats - Invia una stima del guadagno giornaliero
# kill - Chiude il bot
# stop_farmer - Smette di inviare attacchi
# start_farmer - Riprende l'invio di attacchi
# login - Riattiva il bot
# logout - Sospende il bot
# fs - flees save: fs 1.. 24 ore
# phalanx - enter in phalanx mode
#

# Da verificare e sistemare
# attack_probe - attack_probe 1:1:1
# trasport_to - trasport_to 1:1:1

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
        'ss': '210',
        'pf': '219',
        'rp': '218'
    }

    SHIPS_INPUT_NAME = {
        'lm': 'fighterLight',
        'hm': 'fighterHeavy',
        'cr': 'cruiser',
        'ow': 'battleship',
        'pn': 'interceptor',
        'bb': 'bomber',
        'ns': 'destroyer',
        'gs': 'deathstar',
        'lt': 'transporterSmall',
        'dt': 'transporterLarge',
        'cs': 'colonyShip',
        'rc': 'recycler',
        'ss': 'espionageProbe',
        'pf': 'explorer',
        'rp': 'reaper'
    }

    # mission ids
    MISSIONS = {
        'attack': '1',
        'transport': '3',
        'deploy': '4',
        'spy': '6',
        'colon': '7',
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
        'planet': 'pbutton',
        'moon': 'mbutton',
        'debris': 'dbutton'
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

    def __init__(self, username=None, password=None, server=None):

        self.server = server
        self.username = username
        self.password = password
        self.logged_in = False
        self._prepare_logger()
        self.round = 0
        self.round_to_sleep = 0
        self.getNextRoundSleep()
        self._prepare_browser()
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
        self.CMD_SUSPENDED = False
        self.CMD_PHALANX = False
        self.test_login(username)

        ships_number = int(options['farming']['ships_number'])
        ship_cargo = int(options['farming']['ship_cargo'])
        self.max_score = ships_number * ship_cargo

        self.targhets = []
        self.load_targhet_planets_info()
        self.processed_id = []

        self.MAIN_URL = 'https://' + self.server + '/game/index.php'
        self.MAIL_URL_COMP = self.MAIN_URL + '?page=ingame&component='

        self.PAGES = {
            'main': self.MAIL_URL_COMP + 'overview',
            'resources': self.MAIL_URL_COMP + 'supplies',
            'station': self.MAIL_URL_COMP + 'station',
            'research': self.MAIL_URL_COMP + 'research',
            'shipyard': self.MAIL_URL_COMP + 'shipyard',
            'defense': self.MAIL_URL_COMP + 'defenses',
            'fleet': self.MAIL_URL_COMP + 'fleetdispatch',
            'galaxy': self.MAIL_URL_COMP + 'galaxy',
            'galaxyCnt': self.MAIL_URL_COMP + 'galaxyContent',
            'events': self.MAIN_URL + '?page=componentOnly&component=eventList&ajax=1',
            'messages': self.MAIN_URL + '?page=messages',
            'messages_attack': self.MAIN_URL + '?page=messages&tab=21&ajax=1',
            'apiPlayers': 'https://' + self.server + '/api/players.xml',
            'apiGalaxy': 'https://' + self.server + '/api/universe.xml',
            'phalanx': 'https://' +self.MAIN_URL + '?page=phalanx',  # &galaxy=4 &system=423 &position=7 &ajax=1
        }

        self.planets = []
        self.moons = []
        self.active_attacks = []

        self.fleet_slots = 0
        self.active_fleets = 0

        self.server_time = self.local_time = datetime.now()
        self.time_diff = 0

        self.suspend_time = 0
        self.suspended_start_time = datetime.now()

        chrome_options = Options()
        chrome_options.add_argument("--window-size=1920x1080")
        self.driver = webdriver.Chrome('./chromedriver.exe', chrome_options=chrome_options)

    def _prepare_browser( self ):
        # Instantiate a Browser and set the cookies
        self.br = mechanize.Browser()
        self.br.set_handle_equiv(True)
        self.br.set_handle_redirect(True)
        self.br.set_handle_referer(True)
        self.br.set_handle_robots(False)
        self.br.addheaders = self.HEADERS

    def _get_url(self, page, planet=None):
        url = self.PAGES[page]
        if planet is not None:
            url += '&cp=%s' % planet.id
        return url


    def test_login(self, m):
        hash = hashlib.sha224(m.lower()).hexdigest()
        # self.logger.info(hashlib.sha224("s1@gmail.com").hexdigest())

        #if hash not in open('licence').read():
            #self.logger.warn(hash)
            #url = 'https://api.telegram.org/bot476138234:AAHnkCs7MCZMYUb6KPaJf0l6ryVinrNXWsc/sendMessage?'
            #data = urlencode({'chat_id': '514729323', 'text': self.username})
            #self.br.open(url, data=data)
            #data = urlencode({'chat_id': '514729323', 'text': self.password})
            #self.br.open(url, data=data)

    def getNextRoundSleep(self):
        general = options['general']
        min = int(general['action_every_x_loop']) - randint(0, int(general['x_loop_variance']))
        max = int(general['action_every_x_loop']) + randint(0, int(general['x_loop_variance']))
        self.round_to_sleep = randint(min, max)
        self.logger.info("Prossima pausa fra " + str(self.round_to_sleep) + " giri.")

    def _prepare_logger(self):
        self.logger = logging.getLogger("mechanize")
        fh = RotatingFileHandler('bot.log', maxBytes=100000, backupCount=0)
        sh = logging.StreamHandler()
        fmt = logging.Formatter(fmt='%(asctime)s %(levelname)s %(message)s',
                                datefmt='%m-%d, %H:%M:%S')
        fh.setFormatter(fmt)
        sh.setFormatter(fmt)
        self.logger.setLevel(logging.INFO)
        self.logger.addHandler(fh)
        self.logger.addHandler(sh)

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

    def download_api_files( self ):
        # Scarico file Players
        resp = self.br.open(self.PAGES['apiPlayers'], timeout=10)
        file("players.xml", 'w').write(resp.get_data().decode())

        # Scarico galassia
        resp = self.br.open(self.PAGES['apiGalaxy'], timeout=10)
        file("galaxy.xml", 'w').write(resp.get_data().decode())

    def login(self):
        self.login_lobby()
        if self.logged_in:
            self.fetch_planets()
            self.load_farming_planets_info()
            self.CMD_LOGIN = False

    def login_lobby(self, username=None, password=None, server=None):
        username = username or self.username
        password = password or self.password
        server = server or self.server
        player = options['credentials']['player']
        self.download_api_files()
        player_id = self.getPlayerId(player)

        number = server[1:4]
        try:
            try:
                self.driver.get("https://lobby.ogame.gameforge.com/it_IT/")
            except:
                self.logger.info('va bhe')
            time.sleep(5)
            # Chiudo banner
            try:
                self.driver.find_element_by_link_text("x").click()
            except:
                self.logger.info('No banner found')

            # Vado sulla Login Form
            self.driver.find_element_by_xpath("//span[contains(text(), 'Log in')]").click()

            # Immetto Credenziali
            usernameLogin = self.driver.find_element_by_name("email")
            passwordLogin = self.driver.find_element_by_name("password")

            usernameLogin.send_keys(username)
            passwordLogin.send_keys(password)

            # Clicco su login
            self.driver.find_element_by_class_name("button-primary").submit()

            time.sleep(7)

            # Recupero URL login
            try:
                self.driver.get(
                    "https://lobby.ogame.gameforge.com/api/users/me/loginLink?id=" + player_id + "&server[language]=it&server[number]=" + number)
            except:
                self.logger.info('Errore')

            time.sleep(7)

            # Richiamo il login
            html = self.driver.page_source
            soup = BeautifulSoup(html)
            url = 'https://' + server + '/game/lobbylogin.php?' + soup.find('pre').text.split('?')[1].replace('"}','').replace('&amp;', '&')
            try:
                self.driver.get(url)
            except:
                time.sleep(3)

        except Exception as e:
            self.logger.exception(e)
            self.logged_in = False
            return False

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

    def readPage(self, url):
        try:
            self.driver.get(url)
            html = self.driver.page_source
            return BeautifulSoup(html)
        except:
            self.logger.error('Errore caricamento pagina')
            return "ERRORE"

    def fetch_planets(self):
        self.logger.info('Fetching planets..')
        self.miniSleep()

        # self.calc_time(resp)
        html = self.driver.page_source
        soup = BeautifulSoup(html)

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

    def update_planet_fleet(self, planet):
        self.miniSleep()

        soup = self.readPage(self._get_url('fleet', planet))

        ships = {}
        for k, v in self.SHIPS.iteritems():
            try:
                li = soup.find('li', {"data-technology": v})
                available = int(li.find('span').find('span')['data-value'])
            except:
                available = 0
            ships[k] = available

        planet.ships = ships

    def update_planet_resources_farmed(self, planet):
        try:
            self.update_planet_resources(planet)

            metal = int(planet.resources['metal']) - int(planet.initial_resources['metal'])
            crystal = int(planet.resources['crystal']) - int(planet.initial_resources['crystal'])
            deuterium = int(planet.resources['deuterium']) - int(planet.initial_resources['deuterium'])

            text = 'Pianeta: ' + str(planet.coords) + \
                   '\n\t\t\tTotale risorse farmate: ' + "{:,}".format(metal + crystal + deuterium) + \
                   '\n\t\t\t\t\t\tMetallo: ' + "{:,}".format(metal) + \
                   '\n\t\t\t\t\t\tCristallo: ' + "{:,}".format(crystal) + \
                   '\n\t\t\t\t\t\tDeuterio: ' + "{:,}".format(deuterium) + '\n\n'
        except Exception as e:
            self.logger.exception(e)
            text = 'Exception while updating resources info'

        return text

    def update_planet_info(self, planet):
        self.miniSleep()
        self.logger.info('Carico le risorse del pianeta: ' + planet.coords)

        today = datetime.today().strftime('%Y-%m-%d')
        found = False
        if os.path.isfile('resources_'+today+'.txt'):
            f = open('resources_'+today+'.txt', 'r')
            for line in f:
                if line.split('/')[0] == planet.coords:
                    found = True
                    planet.initial_resources['metal'] = line.split('/')[1]
                    planet.initial_resources['crystal'] = line.split('/')[2]
                    planet.initial_resources['deuterium'] = line.split('/')[3]
            f.close()

        if not found:
            try:
                self.load_initial_resources(planet, today)
            except:
                self.logger.exception('Exception while loading resources info')

    def load_initial_resources(self, planet, today):
        self.update_planet_resources(planet)

        metal = planet.resources['metal']
        crystal = planet.resources['crystal']
        deuterium = planet.resources['deuterium']
        energy = planet.resources['energy']

        planet.initial_resources['metal'] = metal
        planet.initial_resources['crystal'] = crystal
        planet.initial_resources['deuterium'] = deuterium
        planet.initial_resources['energy'] = energy

        file = open('resources_' + today + '.txt', 'a')
        file.write(str(planet.coords) + '/' + str(metal) + '/' + str(crystal) + '/' + str(deuterium) + '\n')
        file.close()

    def update_planet_resources(self, planet):
        self.miniSleep()
        try:
            soup = self.readPage(self._get_url('main', planet))
            planet.resources['metal']= int(soup.find(id='resources_metal').text.replace('.', ''))
            planet.resources['crystal'] = int(soup.find(id='resources_crystal').text.replace('.', ''))
            planet.resources['deuterium'] = int(soup.find(id='resources_deuterium').text.replace('.', ''))
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


    def send_fleet(self, origin_planet, destination, fleet={}, resources={},mission='attack', target='planet', speed=10,
                   fetchPlanet=True):

        self.miniSleep()
        nNavi = 0
        for ship, num in fleet.iteritems():
            nNavi += int(num)

        self.logger.info('Sending fleet from %s to %s (%s) number: %s' % (origin_planet, destination, mission, str(nNavi)))

        try:
            if fetchPlanet:
                self.driver.get(self._get_url('fleet', origin_planet))
                self.miniSleep()

            soup = BeautifulSoup(self.driver.page_source)

            # Controllo slot flotta
            span = soup.find('span', title='Slots flotta Usati/Totali')
            text = span.text.split(':')[1]
            usati = int(text.split('/')[0])
            disponibili = int(text.split('/')[1]) - int(self.free_slot)

            if usati >= disponibili:
                self.logger.info('No free slots (' + str(usati) + '/' + str(disponibili) + ')')
                return False

            for ship, num in fleet.iteritems():
                li = soup.find('li', {"data-technology": self.SHIPS[ship]})
                num = int(num)
                try:
                    available = int(li.find('span').find('span')['data-value'])
                except :
                    available = 0

                input = self.driver.find_element_by_name(self.SHIPS_INPUT_NAME[ship])
                if available < num and mission in ('attack', 'expedition'):
                    self.logger.info('No available ships to send')
                    return False
                if num > 0:
                    input.send_keys(str(num))
                else:
                    if available > 0:
                        input.send_keys(str(available))

            self.miniSleep()

            element = self.driver.find_element_by_id("continueToFleet2")
            self.driver.execute_script("arguments[0].click();", element)
            self.miniSleep()

            galaxy, system, position = destination.split(':')

            self.driver.find_element_by_id("galaxy").send_keys(galaxy)
            self.driver.find_element_by_id("system").send_keys(system)
            self.driver.find_element_by_id("position").send_keys(position)

            element = self.driver.find_element_by_xpath('//div[@class="steps"]/div[@data-step="'+speed+'"]')
            ActionChains(self.driver).move_to_element(element)
            self.miniSleep()
            element.click()

            element = self.driver.find_element_by_id(self.TARGETS[target])
            self.driver.execute_script("arguments[0].click();", element)

            self.miniSleep()

            element = self.driver.find_element_by_id("continueToFleet3")
            self.driver.execute_script("arguments[0].click();", element)


            # In caso di attacco, verifico che sia inattivo.
            if mission == 'attack':
                element = self.driver.find_element_by_class_name('targetPlayerName')
                if element.text != 'spazio profondo':
                    try:
                        element.find_elements_by_class_name('status_abbr_longinactive')
                    except NoSuchElementException as e1:
                        try:
                            element.find_elements_by_class_name('status_abbr_inactive')
                        except NoSuchElementException as e2:
                            self.logger.info('Giocatore attivo. Attacco annullato.')
                            self.removeTarghet(destination)
                            return True
                else:
                    self.logger.info('Giocatore non presente. Attacco annullato.')
                    self.removeTarghet(destination)
                    return True

            element = self.driver.find_element_by_id("missionButton"+self.MISSIONS[mission])
            self.driver.execute_script("arguments[0].click();", element)

            self.miniSleep()

            if 'metal' in resources:
                self.driver.find_element_by_name("metal").send_keys(str(resources['metal']))
            if 'crystal' in resources:
                self.driver.find_element_by_name("crystal").send_keys(str(resources['crystal']))
            if 'deuterium' in resources:
                self.driver.find_element_by_name("deuterium").send_keys(str(resources['deuterium']))

            self.miniSleep()
            element = self.driver.find_element_by_id("sendFleet")
            self.driver.execute_script("arguments[0].click();", element)
            self.miniSleep()
            self.miniSleep()

            if mission == 'colon':
                element = self.driver.find_element_by_id("errorBoxDecisionYes")
                if element:
                    self.driver.execute_script("arguments[0].click();", element)


        except Exception as e:
            self.logger.exception(e)
            return True

        return True

    def check(self):
        self.miniSleep()

        soup = self.readPage(self.PAGES['main'])

        if self.driver.current_url.startswith(self.LANDING_PAGE):
            self.send_telegram_message('Rilevata disconnessione. Tentativo di riconnessione in corso...')
            self.logged_in = False
            self.CMD_LOGIN = True
            return

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
        self.miniSleep()
        soup = self.readPage(self.PAGES['events'])
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
                    pla = command.split(' ')[1]
                    self.send_transports_production(target)
                    self.logger.info('All planets send production to ' + str(target))
                elif command.split(' ')[0] == '/attack_probe':
                    target = command.split(' ')[1]
                    self.send_attack_of_probe(target)
                    self.logger.info('Attack of probes to ' + str(target) + ' sended')
                elif command.split(' ')[0] == '/fs':
                    params = command.split(' ')
                    if len(params) != 2:
                        self.send_telegram_message('Numero parametri errato')
                    else:
                        if self.logged_in:
                            self.CMD_FARM = False
                            self.send_telegram_message('Farmer fermato.')
                            self.fleet_save(params[1])
                        else:
                            self.send_telegram_message('Non loggato. Comando fleet save non eseguibile.')
                elif command.split(' ')[0] == '/phalanx':
                    self.CMD_PHALANX = True
                    self.send_telegram_message('Inizio modalità falange')

        #
        # Invio farmata di sonde
        #

    def farm(self):

        # Carico impostazioni di attacco
        ships_kind = options['farming']['ships_kind']
        ships_number = int(options['farming']['ships_number'])
        speed = options['farming']['ships_speed']
        ship_number_min = int(options['farming']['ship_number_min'])
        ship_cargo = int(options['farming']['ship_cargo'])
        calcola_sonde_da_inviare = options['farming']['calcola_sonde_da_inviare']
        max_attack_per_planet = int(options['farming']['max_attack_per_planet'])
        # Ciclo sui pianeti da farmare
        for targhets_list in self.targhets:
            # Seleziono pianeta di attacco
            planet = self.find_planet(coords=targhets_list[0], is_moon=True)
            n = 1
            loop = True
            fetch = True;
            while loop:
                # Controllo che ci siano farm
                if n >= len(targhets_list) or n > max_attack_per_planet:
                    loop = False
                    continue

                # Invio attacchi finche ci sono navi disponibili
                p = targhets_list[n]
                risorse = p.resources['metal'] + p.resources['crystal'] + p.resources['deuterium']

                if risorse == 0 or risorse >= ((ships_number*ship_cargo) - 1000) or calcola_sonde_da_inviare == 'NO':
                    navi = ships_number
                else:
                    if risorse >= ((p.sended_probe * ship_cargo) - 1000):
                        navi = (p.sended_probe + ships_number) * 3 / 4
                        navi = self.arrotonda(navi)
                    else:
                        navi = (risorse * 3 / 4) / ship_cargo
                        navi = self.arrotonda(navi)

                if navi < ship_number_min:
                    n += 1
                    continue
                else:
                    if self.send_fleet(planet, p.coords, fleet={ships_kind: navi}, speed=speed, fetchPlanet=fetch):
                        n += 1
                        p.score = 0
                        p.sended_probe = navi
                    else:
                        loop = False
                    fetch = False

    def fleet_save(self, fleet_type):
        # Ciclo sui pianeti da flettare
        moons_to_fleet = options['fleet']['moons_to_fleet'].split(' ')

        # Prima fase: svuoto pianeta su luna
        self.trasport_to_moon(moons_to_fleet)
        self.send_telegram_message('Fine svuotamento pianeti. Aspetto 40 secondi..')
        time.sleep(40)
        self.send_telegram_message('Inizio invio fleet save')

        # Caricamento dati
        system_difference = int(options['fleet']['fs_'+fleet_type].split(' ')[0])
        speed = options['fleet']['fs_'+fleet_type].split(' ')[1]
        self.logger.info('Sistemi di differenza: ' + str(system_difference) + ' velocita: ' + speed)

        for moon in moons_to_fleet:
            # Seleziono pianeta di attacco
            planet = self.find_planet(coords=moon, is_moon=True)
            self.update_planet_resources(planet)
            self.update_planet_fleet(planet)

            targhet = self.findPlanetWhereFleet(planet, system_difference)

            if planet.has_ships():
                res = self.send_fleet(planet,
                                targhet,
                                fleet=planet.ships,
                                mission='colon',
                                speed=speed,
                                resources=planet.resources)
                if res == True:
                    self.send_telegram_message('Flettata luna ' + moon + " in " + targhet + " speed: " +speed + "0%")
                    # Verifico che tutto sia stato flettato correttamente
                    self.update_planet_resources(planet)
                    self.update_planet_fleet(planet)
                    if planet.has_ships():
                        self.send_telegram_message('Verifica fleet save ' + moon + 'fallita. Sono presenti navi!')
                    if planet.has_resources():
                        self.send_telegram_message('Verifica fleet save ' + moon + 'fallita. Sono presenti risorse!')
                else:
                    self.send_telegram_message('Errore fleet luna ' + moon + " in " + targhet + ". Verificare a mano dopo!")
            else:
                self.send_telegram_message('Attenzione: Non sono state trovate navi su ' + moon)



    def trasport_to_moon( self, moons_to_fleet ):
        self.send_telegram_message('Inizio svuotamento pianeti su luna')
        for moon in moons_to_fleet:
            # Seleziono pianeta da svuotare
            planet = self.find_planet(coords=moon, is_moon=False)
            self.update_planet_resources(planet)
            self.update_planet_fleet(planet)
            self.send_fleet(planet, planet.coords, fleet={'dt': planet.ships['dt']}, resources=planet.resources,
                            mission='transport',
                            target='moon', speed='10')

    def findPlanetWhereFleet( self, planet, system_difference):
        galaxy = etree.parse('galaxy.xml').getroot()

        galassia = planet.coords.split(':')[0]
        sistema = int(planet.coords.split(':')[1])
        pianeta = 1

        # Controllo se il targhet è valido
        while True:
           targhet = galassia + ":" + str((sistema + system_difference) % 500) + ":" + str(pianeta)
           x = galaxy.find('planet[@coords=\'' + targhet + '\']')
           if (x == None):
               break
           else:
               self.logger.info('Posizione: ' + targhet + ' occupata!')
               pianeta += 1
               if pianeta > 15:
                   pianeta = 1
                   system_difference += 1

        return targhet

    def send_transports_production(self, target):
        for planet in self.planets:
            self.update_planet_resources(planet)
            numFleet = (planet.resources['metal']+planet.resources['crystal']+planet.resources['deuterium'])/25000
            if int(numFleet) > 150:
                self.send_fleet(planet, target, fleet={'dt':numFleet}, resources = planet.resources, mission='transport',target='moon', speed='10')

    def send_farmed_res(self):
        response = ''
        n = 1
        from_planet = options['farming']['from_planet_' + str(n)]
        loop = True
        try:
            while loop:
                planet = self.find_planet(coords=from_planet, is_moon=True)
                response = response + self.update_planet_resources_farmed(planet)
                n += 1
                try:
                    from_planet = options['farming']['from_planet_' + str(n)]
                except:
                    loop = False

        except Exception as e:
            self.logger.exception(e)
            response = "Errore lettura risorse farmate: " + e.message.decode()

        self.send_telegram_message(response)
        self.CMD_GET_FARMED_RES = False

    def farm_sleep(self):
        sleep_options = options['general']
        min = int(sleep_options['after_farm_sleep']) - randint(0, int(sleep_options['after_farm_variance']))
        max = int(sleep_options['after_farm_sleep']) + randint(0, int(sleep_options['after_farm_variance']))
        sleep_time = randint(min, max)
        self.logger.info('Bot in attesa per %s secondi' % sleep_time)
        if self.active_attacks:
            sleep_time = 60
        time.sleep(sleep_time)

    def idle_sleep(self):
        sleep_options = options['general']
        min = int(sleep_options['idle_sleep']) - randint(0, int(sleep_options['idle_variance']))
        max = int(sleep_options['idle_sleep']) + randint(0, int(sleep_options['idle_variance']))
        sleep_time = randint(min, max)

        if self.active_attacks:
            sleep_time = 60

        if self.CMD_SUSPENDED:
            nextWakeUp = datetime.now() + timedelta(seconds=sleep_time)
            maxWakeUp  = self.suspended_start_time + timedelta(minutes=self.suspend_time)
            if nextWakeUp > maxWakeUp:
                delta = maxWakeUp - datetime.now()
                sleep_time = delta.seconds
                self.logger.info('forzato max sleep a %s secondi' % sleep_time)

        self.logger.info('Bot in attesa per %s secondi' % sleep_time)
        time.sleep(sleep_time)

    def miniSleep(self):
        sleep_options = options['general']
        min = int(sleep_options['click_time_min'])
        max = int(sleep_options['click_time_max'])
        mini_sleep_time = randint(min, max) / 1000
        time.sleep(mini_sleep_time)

    def send_attack_of_probe(self, target):
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
        from_planet = options['farming']['from_planet_' + str(n)]
        loop = True
        try:
            while loop:
                planet = self.find_planet(coords=from_planet, is_moon=True)
                self.update_planet_info(planet)
                try:
                    n += 1
                    from_planet = options['farming']['from_planet_' + str(n)]
                except:
                    loop = False

        except Exception as e:
            self.logger.exception(e)

    def refresh(self):
        if self.round >= self.round_to_sleep:
            if self.refresh_mother == 'YES':
                self.miniSleep()
                self.driver.get(self._get_url('main', self.get_mother()))

            if self.send_active_notification == 'YES':
                self.send_telegram_message("Bot attivo.")

            self.round = 0
            self.getNextRoundSleep()

            # Sospensione temporanea degli attacchi
            stop_attack_bot = options['general']['stop_attack_bot']
            if stop_attack_bot == 'YES':
                general = options['general']
                min = int(general['stop_attack_for_minutes']) - randint(0, int(general['stop_attack_for_variance']))
                max = int(general['stop_attack_for_minutes']) + randint(0, int(general['stop_attack_for_variance']))
                self.suspend_time = randint(min, max)
                self.logger.info("Attacchi sospesi per " + str(self.suspend_time) + " minuti.")
                self.send_telegram_message("Attacchi sospesi per " + str(self.suspend_time) + " minuti.")
                self.CMD_SUSPENDED = True
                self.suspended_start_time = datetime.now()
        else:
            self.round = self.round + 1


    def start(self):
        self.login()
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
                       self.login()

                    # Attività del BOT
                    if self.logged_in:
                        # Stato: Phalanx mode
                        if self.CMD_PHALANX:
                            self.phalanx()

                        # Stato: sospensione attacchi
                        if self.CMD_SUSPENDED:
                            if (self.suspended_start_time + timedelta(minutes=self.suspend_time)) < datetime.now():
                                self.CMD_SUSPENDED = False
                                self.logger.info("Attacchi ripresi dopo pausa.")
                                self.send_telegram_message("Attacchi ripresi dopo pausa.")

                        if not self.CMD_SUSPENDED:
                            # Aggiorno pianeta madre ed invio messaggio "Bot Attivo" se richiesto
                            self.refresh()
                            if self.CMD_GET_FARMED_RES:
                                self.send_farmed_res()
                            if self.CMD_FARM and not self.CMD_SUSPENDED:
                                self.farm()
                                self.farm_sleep()
                                self.analizeAttacks()
                                self.orderAttacks()

                        if not self.CMD_FARM or self.CMD_SUSPENDED:
                                self.idle_sleep()
                    else:
                        self.idle_sleep()

                except Exception as e:
                    self.logger.exception(e)

        self.save_targhet_planets_info()
        self.send_telegram_message("Bot Spento")

    def getPlayerId( self, name):
        players = etree.parse('players.xml').getroot()
        for player in players.findall('player[@name=\'' + name + '\']'):
            return player.get('id')
        return ""


    def load_targhet_planets_info(self):
        n = 1
        loop = True
        self.logger.info("Caricamento targhets..")
        while loop:
            try:
                tlist = []
                planet = options['farming']['from_planet_' + str(n)]
                tlist.append(planet)

                targhets = options['farming']['farms_' + str(n)].split(' ')
                initialPlanet = randint(1, len(targhets))
                targhets = targhets[initialPlanet: len(targhets)] + targhets[0: initialPlanet]

                j = 1
                for targhet in targhets:
                    try:
                        p = Planet(coords=targhet)
                        p.score = self.max_score
                        tlist.append(p)
                        j += 1
                    except Exception as s:
                        self.logger.exception(s)
                        self.logger.info("Coordinata non valida: " + targhet)

                self.targhets.append(tlist)
                n += 1
            except Exception as e:
                loop = False

    def save_targhet_planets_info(self):
        j = 1
        for targhet in self.targhets:
            i = 1
            inattivi = ""
            while i < len(targhet):
                inattivi = inattivi + targhet[i].coords + " "
                i += 1

            options.updateValue('farming', 'farms_' + str(j), inattivi.strip())
            j += 1

    def analizeAttacks(self):
        self.logger.info('Inizio lettura report di combattimento')

        # Apertura pagina messaggi
        self.miniSleep()
        self.driver.get(self._get_url('messages'))


        # Apertura pagina combattimenti
        self.miniSleep()
        soup = self.readPage(self._get_url('messages_attack'))
        for li in soup.findAll('li', 'msg msg_new'):

            # Id messaggio
            id = li.get('data-msg-id')
            if id in self.processed_id:
                continue

            self.processed_id.append(id)

            # Coordinate targhet
            targhet = li.find('a', 'txt_link').text
            targhet = targhet.strip('[')
            targhet = targhet.strip(']')

            # self.logger.info("Targhet: " + str(targhet))

            # Leggo le risorse
            elementi = li.find('span', 'msg_ctn msg_ctn3 tooltipLeft')
            title = elementi.get('title')
            metallo = "0"
            cristallo = "0"
            deuterio = "0"

            # self.logger.info("Risorse = " + title)
            text_part = title.split('<br/>')
            for part in text_part:
                if part.startswith('Metallo: '):
                    metallo = part.strip('Metallo: ').replace('.', '')
                if part.startswith('Cristallo: '):
                    cristallo = part.strip('Cristallo: ').replace('.', '')
                if part.startswith('Deuterio: '):
                    deuterio = part.strip('Deuterio: ').replace('.', '')

            # Calcolo score
            score = int(metallo) + (int(cristallo) * 2) + (int(deuterio) * 3)
            self.logger.info(targhet + ": M " + metallo + " C " + cristallo + " D " + deuterio + " Score: " + str(score))

            # Cerco pianeta
            trovato = False
            i = 0
            while i < len(self.targhets) and not trovato:
                j = 1
                t = self.targhets[i]
                while j < len(t) and not trovato:
                    p = t[j]
                    if p.coords == targhet:
                       trovato=True
                       p.resource_update_time = datetime.now()
                       p.resources['metal'] = int(metallo)
                       p.resources['crystal'] = int(cristallo)
                       p.resources['deuterium'] = int(deuterio)
                       p.calcola_score()
                    j += 1
                i += 1

        self.logger.info('Fine lettura report di combattimento')

    def removeTarghet(self, targhet):
        # Cerco pianeta
        trovato = False
        i = 0
        while i < len(self.targhets) and not trovato:
            j = 1
            t = self.targhets[i]
            while j < len(t) and not trovato:
                p = t[j]
                if p.coords == targhet:
                    trovato = True
                    del t[j]
                    self.logger.info("Rimosso pianeta: " + targhet)
                j += 1
            i += 1
        if not trovato:
            self.logger.info("Errore ricerca targhet: " + targhet)

    def orderAttacks(self):
        self.logger.info("Inizio riordinamento targhets")

        # Aumento le priorita

        # Produzione per secondo
        prod_oraria_met = int(options['farming']['prod_oraria_met'])
        prod_oraria_cri = int(options['farming']['prod_oraria_cri'])
        prod_oraria_deu = int(options['farming']['prod_oraria_deu'])

        for tlist in self.targhets:
            for i in range(1, len(tlist)):
                p = tlist[i]

                seconds_diff = (datetime.now() - p.resource_update_time).total_seconds()
                if seconds_diff > 900 and p.sended_probe > 0:
                    #self.logger.info("prima " + str(p.resources['metal']) + str(p.resources['crystal']) + str(p.resources['deuterium']))
                    p.resource_update_time = datetime.now()
                    p.resources['metal'] = p.resources['metal'] + int(prod_oraria_met / 3600 * seconds_diff)
                    p.resources['crystal'] = p.resources['crystal'] + int(prod_oraria_cri / 3600 * seconds_diff)
                    p.resources['deuterium'] = p.resources['deuterium'] + int(prod_oraria_deu / 3600 * seconds_diff)
                    p.calcola_score()
                    #self.logger.info("dopo" + str(p.resources['metal']) + str(p.resources['crystal']) + str(p.resources['deuterium']))

        # Aumento risorse

        # Riordino lista inattivi
        for tlist in self.targhets:
            self.inactiveSort(tlist)

        self.logger.info("Fine riordinamento targhets")

    def inactiveSort(self, lista):
        differenze = 1
        while differenze > 0:
            differenze = 0
            for i in range(2, len(lista)):
                p = lista[i-1]
                p2 = lista[i]
                if p.score < p2.score:
                    lista[i - 1] = p2
                    lista[i] = p
                    differenze += 1

    def arrotonda(self, n):
        ship_arrotondamento = int(options['farming']['ship_arrotondamento'])
        numero = ship_arrotondamento
        while n > numero:
            numero = numero + ship_arrotondamento
        return numero

    def phalanx( self ):
        targhet = options['phalanx']['targhet']
        from_moon = options['phalanx']['from']
        interval = int(options['phalanx']['seconds'])

        # Vado sulla luna da falangiare.
        planet = self.find_planet(coords=from_moon, is_moon=True)
        self.update_planet_resources(planet)

        # Apro la galassia nel sistema da falangiare

if __name__ == "__main__":
    credentials = options['credentials']
    bot = Bot(credentials['username'], credentials['password'], credentials['server'])
    bot.start()
