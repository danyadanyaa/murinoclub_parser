import requests
import json
import urllib3
import re
import time
import logging
from time import strptime
import sys
from bs4 import BeautifulSoup, Tag
from urllib.parse import urljoin, urlparse
from decimal import Decimal

from urllib.error import HTTPError

urllib3.disable_warnings()
logger = logging.getLogger()
# logger.setLevel(logging.DEBUG)
# if sys.platform == 'darwin':
#     logging.basicConfig(stream=sys.stdout, level=logging.DEBUG)
# else:
#     logging.basicConfig(stream=sys.stdout, level=logging.CRITICAL)


class EstateObject():

    possible_types = ['flat', 'apartment', 'parking', 'commercial',
                      'storeroom', 'townhouse']

    empty_values = ['null', '-', '0', '–', 'no', '—']

    type_by_names = {
        'commercial': ['нежилое помещение', 'нежилое', 'помещение', 'ритейл', 'псн', 'коммерческое', 'офис',
                       'бизнес', 'street retail'],
        'flat': ['жилое помещение', 'квартира', 'пентхаус', 'лофт', 'студия'],
        'apartment': ['апартамент', 'апаратамен', 'сьют', 'аппартамент', ],
        'storeroom': ['кладов', 'келлер', 'storage', 'хоз. блок'],
        'parking': ['машиноместо', 'гараж', 'место для мотоцикла', 'парк'],
        'townhouse': ['таунхаус', 'дуплекс'],
        'SKIP_TYPE': ['инвестиционные проекты', 'вилла', 'участок', 'шале', 'дом', 'особняк', 'торговый центр',
                      'арендный бизнес', 'сapital markets', 'гостиница']
    }

    def __init__(self, site_url=None, validate_price=True, **kwargs):
        """
        Note: attributes with started with '_' will be deleted by saving object
        name all helping attributes only with first '_'

        Through kwargs you can fill any attribute by creating obj
        """
        self._site_url = site_url
        self._ignore_small_prices = False
        self._validate_price = validate_price
        self._minimal_allowed_price = 500000
        self._used_rooms_for_search_liv_area = False
        self._in_sale_statuses = []
        self._not_in_sale_statuses = []
        self._reserved_statuses = []
        self._correct_type_dynamic = False
        self._swap_wrong_prices = False
        self._ignore_empty_rooms = False
        self._split_floors = False
        self._floors = None
        self._project_price_multi = None
        self._skip_wrong = False
        self._auto_correct_price = False
        self._validate_data = True
        self._need_save = True
        self._resort_obj_types()
        # self._mapper = TableMapper()
        for key, value in kwargs.items():
            setattr(self, key, value)

        self.complex = None
        self.type = 'flat'
        self.comissioning = None
        self.building = None
        self.section = None
        self.price = None
        self.price_base = None
        self.area = None
        self.number = None
        self.number_on_site = None
        self.rooms = None
        self.floor = None
        self.in_sale = 1
        self.finished = None

        self.sale_status = None
        self.living_area = None
        self.ceil = None
        self.article = None
        self.finishing_name = None
        self.price_sale = None
        self.price_finished = None
        self.price_finished_sale = None
        self.furniture_price = None
        self.furniture = None
        self.plan = None
        self.feature = None
        self.view = None
        self.euro_planning = None
        self.sale = None
        self.discount_percent = None
        self.discount = None
        self.flat_url = None

    def _resort_obj_types(self):
        pairs = []
        for obj_type, text_names in self.type_by_names.items():
            for text_name in text_names:
                pairs.append((obj_type, text_name))
        pairs.sort(key=lambda x: len(x[1]), reverse=True)
        self._type_by_names = pairs

    @staticmethod
    def remove_restricted(value, restricted):
        if isinstance(value, str):
            value = value.strip()
            for part in restricted:
                value = re.sub(part, '', value, flags=re.I).strip()
        return value

    @staticmethod
    def correct_decimal_delimeter(value):
        if isinstance(value, str):
            return value.replace(',', '.')
        return value

    def set_complex(self, value):
        restricted_parts = ['\t', '\n', '\xad', '(дом сдан)', 'дом сдан']
        value = self.remove_restricted(value, restricted_parts)
        value = value.title().replace('Жк', 'ЖК')
        self.complex = value

    def find_obj_type_by_value(self, value):
        for obj_type, text_name in self._type_by_names:
            if text_name in value.lower():
                return obj_type

    def set_obj_type(self, value):
        if value not in self.possible_types:
            found_type = self.find_obj_type_by_value(value)
            if found_type:
                if found_type == 'SKIP_TYPE':
                    self._need_save = False
                self.type = found_type
            else:
                raise Exception('Fount new obj type name', value)
            if 'пентхаус' in value:
                self.set_feature('Пентхаус')
        else:
            self.type = value

    def set_building(self, value):
        restricted_parts = ['корпус', 'корп.', 'корп', 'строение',
                            'многоэтажный паркинг', 'подземный паркинг',
                            'паркинг', '№', 'дом', ':',
                            '\t', '\n', 'квартал']
        value = self.remove_restricted(value, restricted_parts)
        if not isinstance(value, str) or value.lower().strip() not in self.empty_values:
            self.building = value

    def set_section(self, value):
        restricted_parts = ['секция', 'парадная', '№', ':', '\t', 'подъезд', 'блок', 'секц.']
        value = self.remove_restricted(value, restricted_parts)
        if value:
            if not isinstance(value, str) or value.lower().strip() not in self.empty_values:
                self.section = value

    def _decode_price(self, value, multi=1):
        if isinstance(value, str):
            if ('запрос' in value.lower() or 'прода' in value.lower() or
                    'брон' in value.lower() or 'указ' in value.lower() or
                    'обсуждае' in value.lower() or
                    'уточн' in value.lower() or 'индивид' in value.lower()):
                return
            if value in self.empty_values:
                return
        restricted_parts = ['cтоимость', 'стоимость', 'рублей', 'цена базовая', 'квартиры',
                            'руб.', 'pуб.', 'p уб.', 'руб', 'цена', 'выгода до', 'выгода', 'млн.',
                            'rub', 'млн', 'от', '₽', 'р.', 'до', '>',
                            'р', ' ', ' ', ':', '’', 'p', r'\s', '!', r'\*']

        value = self.correct_decimal_delimeter(value)
        value = self.remove_restricted(value, restricted_parts)
        if value:
            if self._auto_correct_price:
                value = self.correct_price(value)
            return round(Decimal(value) * multi, 0)

    def correct_price(self, price_str):
        if re.findall(r'\d', price_str):
            # If 4 - 8 млн
            if '-' in price_str:
                price_str = price_str.split('-')[0]
            if '–' in price_str:
                price_str = price_str.split('–')[0]
        if Decimal(price_str) < 1000:
            price_str = round(Decimal(price_str) * 1_000_000, 0)
        return price_str

    def _check_price_value(self, price):
        if price and not self._ignore_small_prices:
            if (price > 0 and price < 10000) or \
                    price > 1000000 * 100000:
                raise Exception('Wrong price value')

    def set_price_base(self, value, sale=None, multi=1):
        if self._project_price_multi:
            multi = self._project_price_multi
        if isinstance(value, str) and '$' in value:
            self.currency = '$'
            value = value.replace('$', '')
        self.price_base = self._decode_price(value, multi)
        if sale:
            price_sale = self._decode_price(sale, multi)
            if price_sale:
                if self.price_base:
                    if price_sale < self.price_base:
                        self.price_sale = price_sale
                    elif price_sale > self.price_base:
                        self.price_sale = self.price_base
                        self.price_base = price_sale
                else:
                    self.price_base = price_sale
        self._check_price_value(self.price_base)

    def _area_cleaner(self, value) -> Decimal:
        # restricted_parts = ['общая', 'площадь', 'м²', 'м2', 'кв.м.', 'кв.м',
        #                     'м', 'жилая', '\t', '\n', ' ']
        value = self.correct_decimal_delimeter(value)
        if isinstance(value, str):
            value = re.findall(r'[+-]?[0-9]*[.]?[0-9]+', value)[0]
        # value = self.remove_restricted(value, restricted_parts)
        return Decimal(value)

    def set_area(self, value):
        if value:
            if not isinstance(value, str) or value.lower().strip() not in self.empty_values:
                self.area = self._area_cleaner(value)

    def check_is_object_type_valid(self, value):
        if isinstance(value, str):
            found_type = self.find_obj_type_by_value(value)
            if found_type and found_type != self.type:
                if self._correct_type_dynamic:
                    self.type = found_type
                else:
                    raise Exception('Find {} in number, but type is {}, extected type: {}'.
                                    format(value, self.type, found_type))

    def set_number(self, value):
        self.check_is_object_type_valid(value)
        restricted_parts = ['помещение свободного назначения',
                            'офис', 'квартира', 'квартиры', '№', 'машиноместо', 'кладовая',
                            'нежилое помещение', 'коммерческое помещение',
                            'паркинг', 'кладовка', 'номер', 'лот', 'помещение', 'апартаменты',
                            'ком.пом.', 'пом.', 'апартамент', 'кв.', 'м/м', 'мот/м', 'м.м']
        value = self.remove_restricted(value, restricted_parts)
        self.number = value

    def set_number_on_site(self, value):
        restricted_parts = ['помещение свободного назначения',
                            'офис', 'квартира', 'квартиры', '№', 'машиноместо', 'кладовая',
                            'нежилое помещение', 'коммерческое помещение',
                            'паркинг', 'кладовка', 'номер', 'лот', 'помещение', 'апартаменты',
                            'ком.пом.', 'пом.', 'апартамент', 'кв.', 'м/м', 'мот/м', 'м.м']
        value = self.remove_restricted(value, restricted_parts)
        self.number_on_site = value

    def set_rooms(self, value, check_euro=True, check_type=True):
        if isinstance(value, str):
            value = value.lower().strip()
            if 'пентхаус' in value:
                self.set_feature('Пентхаус')
                return
            if 'св. план' in value:
                self.set_feature('Свободная планировка')
                return
            value = value.replace('комнаты', '').replace('комната', '').strip()
            if check_euro and 'евро' in value:
                self.euro_planning = 1
            if check_type:
                for obj_type, text_name in self._type_by_names:
                    if text_name in value.lower():
                        if obj_type == 'flat':
                            continue
                        elif obj_type == 'apartment':
                            self.type = obj_type
                            continue
                        else:
                            self.type = obj_type
                            return
            if 'одно' in value or '1-а' in value or 'однушка' in value:
                self.rooms = 1
            elif 'двух' in value or '2-х' in value or 'двушка' in value:
                self.rooms = 2
            elif 'трех' in value or 'трёх' in value or '3-х' in value\
                    or 'трешка' in value or 'трёшка' in value:
                self.rooms = 3
            elif 'четырех' in value or 'четырёх' in value or\
                    '4-х' in value:
                self.rooms = 4
            elif 'пяти' in value:
                self.rooms = 5
            elif 'шести' in value:
                self.rooms = 6
            elif 'семи' in value:
                self.rooms = 7
            elif 'многоком' in value:
                self.rooms = None
            else:
                if 'студия' in value or 'студ' in value or\
                        'studio' in value or value == 'с'\
                        or value == 'c' or value == 's'\
                        or value == 'ст' or value == 'ст.' or\
                        'cтудия' in value or value == '0'\
                        or value == 'cт' or value == 'st':
                    self.rooms = 'studio'
                else:
                    if '-1' not in value:
                        if check_euro and 'e' in value or 'е' in value and len(value) < 4:
                            self.euro_planning = 1
                        if value not in self.empty_values:
                            num = re.findall(r'\d+', value)
                            if num:
                                self.rooms = int(num[0])
                            else:
                                if not self._ignore_empty_rooms:
                                    raise Exception('No digits find in room field', value)
        else:
            self.rooms = int(value)
        if self.rooms == 0:
            self.rooms = 'studio'

    def set_floor(self, value):
        if value and isinstance(value, str):
            if 'цоколь' in value.lower():
                self.floor = -1
                return
            if 'первый' in value.lower():
                self.floor = 1
                return
            if 'подвал' in value.lower():
                self.floor = -1
                return
            if 'из' in value:
                value = value.split('из')[0]
            if '/' in value:
                value = value.split('/')[0]
        if self._split_floors:
            self._floors = Utils.split_floors(value)
        else:
            if value:
                if isinstance(value, str):
                    value = re.findall(r'-?\d+', value)[0]
                if not isinstance(value, str) or value.lower().strip() not in self.empty_values:
                    self.floor = int(value)

    def set_in_sale(self, value=1):
        if isinstance(value, str):
            if 'брон' in value.lower():
                self.set_sale_status('Забронирована')
                value = 1
            elif 'резерв' in value.lower():
                self.set_sale_status('Зарезервирована')
                value = 1
            elif 'reserv' in value.lower():
                self.set_sale_status('Зарезервирована')
                value = 1
            elif 'book' in value.lower():
                self.set_sale_status('Зарезервирована')
                value = 1
            elif 'вторичная продажа' in value.lower():
                self.set_sale_status('Вторичная продажа')
                value = 1
            elif 'закрытые продажи' in value.lower():
                self.set_sale_status('Закрытые продажи')
                value = 1
            elif 'свобод' in value.lower():
                value = 1
            elif 'акция' in value.lower():
                value = 1
                self.set_sale('Акция')
            elif 'выгодное предложение' in value.lower():
                value = 1
            elif 'free' in value.lower():
                value = 1
            elif 'в продаже' in value.lower():
                value = 1
            elif 'продан' in value.lower():
                value = 0
            elif 'sold' in value.lower():
                value = 0
            elif 'false' in value.lower():
                value = 0
            elif 'true' in value.lower():
                value = 0
            elif 'avail' in value.lower():
                value = 1
            elif 'active' == value.lower():
                value = 1
            elif 'sale' == value.lower():
                value = 1
            elif 'unavailable' in value.lower():
                value = 1
        if value in self._in_sale_statuses:
            value = 1
        elif value in self._reserved_statuses:
            value = 1
            self.set_sale_status('Забронировано')
        elif value in self._not_in_sale_statuses:
            value = 0
        if value:
            value = int(value)
        if value not in [0, 1, None]:
            raise Exception('Wrong object in_sale attribute', value)
        self.in_sale = value

    def set_finished(self, value=0):
        if value not in [0, 1, None, 'optional']:
            raise Exception('Wrong object finished attribute', value)
        self.finished = value

    def set_currency(self, value):
        self.currency = value

    # Next go v_2.2 part

    def set_sale_status(self, value):
        restricted_parts = ['статус', ':']
        value = self.remove_restricted(value, restricted_parts)
        self.sale_status = value

    def set_living_area(self, value):
        if value:
            if not isinstance(value, str) or value.lower().strip() not in self.empty_values:
                if self._used_rooms_for_search_liv_area:
                    raise Exception('tried get area from rooms area and from liv_area field')
                self.living_area = self._area_cleaner(value)

    def set_ceil(self, value):
        restricted_parts = ['высота', 'потолков', 'потолки', 'потолка', 'потолок',
                            ':', 'м.', 'м']
        value = self.correct_decimal_delimeter(value)
        value = self.remove_restricted(value, restricted_parts)
        self.ceil = Decimal(value)

    def set_article(self, value):
        restricted_parts = ['типовая', '№', 'артикул:', 'тип планировки', 'тип']
        value = self.remove_restricted(value, restricted_parts)
        self.article = str(value)

    def set_finishing_name(self, value):
        restricted_parts = []
        not_finished = ['без отделки', 'без ремонта', 'нет']
        not_finished.extend(self.empty_values)
        value = self.remove_restricted(value, restricted_parts)
        for finish in not_finished:
            if finish in str(value).lower():
                return
        if value:
            self.set_finished(1)
            finished = ['да', 'есть', '1', 'с отделкой', 'true']
            for finish in finished:
                if finish == str(value).lower():
                    return
            self.finishing_name = value

    def set_price_sale(self, value, multi=1):
        self.price_sale = self._decode_price(value, multi)
        self._check_price_value(self.price_sale)

    def set_price_finished(self, value, sale=None, multi=1):
        self.price_finished = self._decode_price(value, multi)
        self._check_price_value(self.price_finished)

    def set_price_finished_sale(self, value, sale=None, multi=1):
        self.price_finished_sale = self._decode_price(value, multi)
        self._check_price_value(self.price_finished_sale)

    def set_furniture_price(self, value, sale=None, multi=1):
        self.furniture_price = self._decode_price(value, multi)
        self._check_price_value(self.furniture_price)

    def set_furniture(self, value=0):
        if value not in [0, 1, 'optional', None]:
            raise Exception('Wrong object furniture attribute', value)
        self.furniture = value

    def set_comissioning(self, value, time_mask=None):
        """
        Срок ввода для корпуса, формат “IV кв 2023”, “II кв 2021” и т.п.
        Возможно значение просто год ввода “2024” (если на сайте только год) или значение “сдан”
        time_mask:  '%Y-%m-%d'              2022-01-01
                    '%B %Y'                 Март 2022
                    '%Y-%m-%d %H:%M:%S'     2023-12-30 09:31:18

        """
        months = {'Январь': 1, 'Февраль': 2, 'Март': 3, 'Апрель': 4, 'Май': 5, 'Июнь': 6, 'Июль': 7,
                  'Август': 8, 'Сентябрь': 9, 'Октябрь': 10, 'Ноябрь': 11, 'Декабрь': 12}
        if value:
            if any((re.search(s, value, flags=re.I) for s in ['Заселен', 'сдан'])):
                self.comissioning = "сдан"
                return
            if re.search(r'^\d\d\d\d$', value.strip()):
                self.comissioning = value.strip()
                return

            match = re.search(r'(?:январь|февраль|март|апрель|май|июнь|июль|август|сентябрь|октябрь|ноябрь|декабрь)', value, flags=re.I)
            if match:
                value = value.replace(match.group(), str(months[match.group().title()]))

            for r in ['Срок сдачи', 'Сдача', 'год', r'г\.', 'г', ':']:
                value = re.sub(r, '', value, flags=re.I)

            if time_mask:
                time_ = strptime(value, time_mask)
                quartal = time_.tm_mon / 3
                quartal = int(-1 * quartal // 1 * -1)  # округление в большую сторону
                value = f"{quartal} кв {time_.tm_year}"

            value = re.sub(r'([IV\d]+)кв', r'\1 кв', value, flags=re.I)
            value = re.sub('квартал', 'кв', value, flags=re.I)
            value = re.sub('кв\.', 'кв', value, flags=re.I)
            value = re.sub('1 кв', 'I кв', value, flags=re.I)
            value = re.sub('2 кв', 'II кв', value, flags=re.I)
            value = re.sub('3 кв', 'III кв', value, flags=re.I)
            value = re.sub('4 кв', 'IV кв', value, flags=re.I)
            value = value.strip()
            self.comissioning = value

    def set_plan(self, value, base_url=None, add_base_if_none=True):
        if value:
            if isinstance(value, Tag):
                value = value.img['src']
            if base_url:
                value = urljoin(base_url, value)
            if add_base_if_none and 'http' not in value:
                value = urljoin(Utils.get_domain(self._site_url), value)
            self.plan = value

    def set_feature(self, value):
        if value:
            restricted_parts = ['\t', '\n']
            value = self.remove_restricted(value, restricted_parts)
            if 'евро' in value.lower():
                self.euro_planning = 1
                return
            if self.feature:
                if isinstance(self.feature, str):
                    self.feature = [self.feature]
                if value not in self.feature:
                    self.feature.append(value)
            else:
                self.feature = value

    def set_view(self, value):
        if value:
            restricted_parts = ['\t', '\n']
            value = self.remove_restricted(value, restricted_parts)
            if self.view:
                self.view.append(value)
            else:
                self.view = [value]

    def set_euro_planning(self, value):
        value = int(value)
        if value not in [0, 1, None]:
            raise Exception('Wrong object euro_planning attribute', value)
        self.euro_planning = value

    def set_sale(self, value):
        if self.sale:
            self.sale += '; ' + value
        else:
            self.sale = value

    def set_discount_percent(self, value):
        restricted_parts = ['скидка', '%', '-']
        value = self.correct_decimal_delimeter(value)
        value = self.remove_restricted(value, restricted_parts)
        self.discount_percent = Decimal(value)

    def set_discount(self, value):
        self.discount = self._decode_price(value)

    def set_level(self, value):
        if isinstance(value, str) and 'двухуровневая' in value.lower():
            self.set_feature('Двухуровневая')
        elif '2' in str(value):
            self.set_feature('Двухуровневая')

    def set_balcon(self, value, balcon_type='Балкон'):
        if isinstance(value, str):
            if 'терраса' in value.lower():
                self.set_feature('Терраса')
            if 'балкон' in value.lower():
                self.set_feature(balcon_type)
            elif 'лоджия' in value.lower():
                self.set_feature('Лоджия')
            elif 'да' in value.lower():
                self.set_feature(balcon_type)
            elif 'есть' in value.lower():
                self.set_feature(balcon_type)
            elif '+' in value.lower():
                self.set_feature(balcon_type)
            else:
                try:
                    value = self._area_cleaner(value)
                    if value:
                        self.set_feature(balcon_type)
                except:
                    pass
        else:
            if isinstance(value, int) and value:
                self.set_feature(balcon_type)

    def set_loggia(self, value):
        self.set_balcon(value, balcon_type='Лоджия')

    def set_storeroom(self, value):
        self.set_balcon(value, balcon_type='Кладовая')

    def set_terrace(self, value):
        if isinstance(value, str):
            if 'да' in value.lower():
                self.set_feature('Терраса')
            elif 'есть' in value.lower():
                self.set_feature('Терраса')
            elif 'терраса' in value.lower():
                self.set_feature('Терраса')
            else:
                try:
                    value = self._area_cleaner(value)
                    if value:
                        self.set_feature('Терраса')
                except:
                    pass
        else:
            if isinstance(value, int) and value:
                self.set_feature('Терраса')

    def find_living_area_from_rooms(self, room_area):
        value = self._area_cleaner(room_area)
        if self.living_area is None:
            self.living_area = 0
        if not self._used_rooms_for_search_liv_area and self.living_area:
            raise Exception('tried get area from rooms area after get it from liv_area field')
        self.living_area += value
        self._used_rooms_for_search_liv_area = True

    def final_check(self):
        try:
            if self._validate_data:
                self._validate_obj_data()
            if self._validate_price:
                self._validate_prices()
        except:
            if not self._skip_wrong:
                raise
            else:
                self._need_save = False
                return False
        self._clear_rooms_by_not_flats()
        self._set_not_in_sale_if_no_price()
        self._swap_base_price_and_finish_price()
        self._clear_same_prices()

        if self.type not in EstateObject.possible_types:
            raise Exception('Wrong object type', self.type)

        return True

    def _set_not_in_sale_if_no_price(self):
        if not (self.price_base or self.price_sale or self.price_finished or
                self.price_finished_sale):
            self.in_sale = 0

    def _swap_base_price_and_finish_price(self):
        if self.finished == 1 and self.price_base and not self.price_finished:
            self.price_finished = self.price_base
            self.price_base = None

        if self.finished == 1 and self.price_sale and not self.price_finished_sale:
            self.price_finished_sale = self.price_sale
            self.price_sale = None

    def _clear_same_prices(self):
        if self.price_base and self.price_sale:
            if self.price_base == self.price_sale:
                self.price_sale = None

        if self.price_finished and self.price_finished_sale:
            if self.price_finished == self.price_finished_sale:
                self.price_finished_sale = None

    def _clear_rooms_by_not_flats(self):
        if self.type == 'parking' or self.type == 'storeroom':
            self.rooms = None

    def _validate_obj_data(self):
        if isinstance(self.rooms, int) and self.rooms > 10:
            if self.area and self.area < 100:
                raise Exception('too big room count and small area', self.rooms, self.area)
        if isinstance(self.rooms, int) and self.rooms > 30:
            raise Exception('too big room count', self.rooms)
        if self.floor and self.floor > 100:
            raise Exception('too big floor number', self.floor)
        if self.type == 'flat' or self.type == 'apartment':
            if self.area and self.area < 10:
                raise Exception('too small area for flat', self.area)
            if self.area and self.area > 3000:
                raise Exception('too big area for flat', self.area)
        if self.area and self.living_area and self.living_area > self.area:
            raise Exception(f'living_area `{self.living_area}` bigger then area `{self.area}`')
        if self.type == 'parking':
            if self.area and self.area > 50:
                raise Exception('too big area for parking', self.area)
        if self.area and self.area <= 1:
            raise Exception('area <= 1', self.area)

    def _validate_prices(self):
        if self.price_base and self.price_sale:
            if self.price_base < self.price_sale:
                if self._swap_wrong_prices:
                    self.price_base, self.price_sale = self.price_sale, self.price_base
                else:
                    raise Exception('Wrond sale price', self.price_base,
                                    self.price_sale)

        if self.price_finished and self.price_finished_sale:
            if self.price_finished < self.price_finished_sale:
                if self._swap_wrong_prices:
                    self.price_finished, self.price_finished_sale = self.price_finished_sale, self.price_finished
                else:
                    raise Exception('Wrond price_finished_sale price',
                                    self.price_finished,
                                    self.price_finished_sale)

        if self.discount_percent and self.discount_percent > 30:
            raise Exception('Too big discount rate', self.discount_percent)

        if self.type == 'flat' or self.type == 'commercial' or self.type == 'apartment':
            if self.price_sale and self.price_sale < self._minimal_allowed_price:
                raise Exception('Too small price_sale', self.price_sale)
            if self.price_base and self.price_base < self._minimal_allowed_price:
                raise Exception('Too small price_base', self.price_base)
            if self.price_finished_sale and self.price_finished_sale < self._minimal_allowed_price:
                raise Exception('Too small price_finished_sale', self.price_finished_sale)
            if self.price_finished and self.price_finished < self._minimal_allowed_price:
                raise Exception('Too small price_finished', self.price_finished)

    def pre_json(self):
        return {k: v for k, v in self.__dict__.items() if not k.startswith('_')}

    def __eq__(self, other):
        return self.__dict__ == other.__dict__

    def __hash__(self):
        return hash(tuple(sorted(self.__dict__.items())))

    def __repr__(self):
        return str(self.__dict__)


class DecimalEncoder(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, Decimal):
            return float(o)
        return super(DecimalEncoder, self).default(o)


class Utils:

    @staticmethod
    def remove_restricted(value, restricted):
        if isinstance(value, str):
            value = value.strip()
            for part in restricted:
                value = re.sub(part, '', value, flags=re.I).strip()
        return value

    @staticmethod
    def _normalize_str(string):
        return ' '.join(re.sub(r'\s', ' ', string).strip().split())

    @staticmethod
    def get_domain(url):
        return '{uri.scheme}://{uri.netloc}/'.format(uri=urlparse(url))

    @staticmethod
    def parse_post_data(content, use_tuple=False):
        if use_tuple:
            r = []
        else:
            r = {}
        content = content.replace('\r', '\n')
        for line in content.split('\n'):
            if line.strip():
                x = line.split(':')
                k = x[0].strip()
                v = ':'.join(x[1:]).strip()
                if use_tuple:
                    r.append((k, v))
                else:
                    if k in r:
                        raise Exception('{0} уже есть'.format(k))
                    r[k] = v
        return r

    @staticmethod
    def page_reloader(func):
        def wrapper(*args, **kwargs):
            try_number = 0
            while try_number < 10:
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    # print(e)
                    time.sleep(1 + try_number)
            raise Exception('Cant reload page after 10 retries')

        return wrapper

    @staticmethod
    def split_floors(floors_str):
        """
        Split floor string to floors array. Assume that floors are separated
        by '-' if interval (e.g. 2-5 return [2,3,4,5]) and with ',' as enumeration
        (e.g. '2,4,7' return [2,4,7]). Both separators can be used together.
        """
        floors = []
        floors_str = floors_str.strip()
        if floors_str:
            floors_str = re.sub(r'[а-яА-я]', '', floors_str, flags=re.I)
            floors_str = floors_str.replace(';', ',').replace('–', '-')
            if floors_str:
                if '-' in floors_str and ',' in floors_str:
                    for floor in floors_str.split(','):
                        floors.extend(Utils.split_floors(floor))
                elif '-' in floors_str:
                    from_floor, to_floor = floors_str.split('-')
                    for floor in range(int(from_floor), int(to_floor) + 1):
                        floors.append(floor)
                elif ',' in floors_str:
                    floors = [int(floor) for floor in floors_str.split(',') if floor]
                else:
                    floors.append(int(floors_str))
            return floors

    @staticmethod
    def extract_js(var_name, bs=None, text=None):
        if bs:
            script = next(s.text for s in bs.select('script') if var_name in s.text)
            script = script.strip().replace('\n', '')
            script = re.findall(var_name + r'(.*?);', script)[0]
            return json.dumps(script)
        if text:
            text = Utils.remove_comments(text)
            text = text.strip().replace('\n', '')
            pre_js = re.findall(var_name + r'(.*?);', text)[0]
            return json.loads(pre_js)

    @staticmethod
    def remove_comments(string):
        # Stackoverflow solution for removing comment from js code
        pattern = r"(\".*?\"|\'.*?\')|(/\*.*?\*/|//[^\r\n]*$)"
        # first group captures quoted strings (double or single)
        # second group captures comments (//single-line or /* multi-line */)
        regex = re.compile(pattern, re.MULTILINE | re.DOTALL)

        def _replacer(match):
            # if the 2nd group (capturing comments) is not None,
            # it means we have captured a non-quoted (real) comment string.
            if match.group(2) is not None:
                return ""  # so we will return empty to remove the comment
            else:  # otherwise, we will return the 1st group
                return match.group(1)  # captured quoted-string
        return regex.sub(_replacer, string)

    @staticmethod
    def test_out(data, file='Setun.txt'):
        if sys.platform == 'darwin':
              pass
#    with open(file, 'w', encoding='utf-8') as f:
#        f.write(str(data))


class TableMapper:
    """
    Helper class to fill information about estate
    Short info:
        _clean_key, _clean_value - preprocess input data
        map_by_one - get one key, one value and search in method_by_names for intersections
            if found first return correspondent method to call (by obj)
        Next method are wrappers around map_by_one (to choose more suitable):
            map_by_dict - map dictionary where dict_key is key to find
            map - map array of keys and values
            map_by_table - parse 3 types of tables 1. head+rows 2. row=head(th)+value(td) 3. row=head(td)+value(td)

    """
    restricted_keys = ['цена за 1', 'цена за кв.м', 'площадь кухни', 'datePriceIncrease', 'withPriceIncrease',
                       'meterPrice', 'цена руб/м 2']

    method_by_names = [
        (('статус', 'available', 'statusFlat', 'st', 'crm_status', 'SalesStatusText',
          'status', 'isAvailable'), 'set_in_sale'),
        (('количество комнат', 'rooms_count', 'roomsQuantity', 'кол-во комнат', 'тип квартиры',
          'число комнат', 'комнат в квартире', 'roomsNumber', 'rc', 'комнат', 'room_count',
          'rooms', 'roomtype', 'sumRooms', 'crm_rooms', 'roomsCount', 'NumberOfRooms', 'room',
          'room_type'), 'set_rooms'),
        (('общая площадь', 'area', 'fullFlat', 'метраж', 's общ', 'totalSquare', 'square',
          'sq', 'общая пл.', 'общая s', 'общая', 'площадь', 'square_total',
          'crm_area_value', 'totalArea', 'area_all', 'stotal', 'areaTotal'), 'set_area'),
        (('price', 'priceFlat', 'tc', 'цены', 'total_cost', 'priceTotal',
          'цена', 'стоимость', 'totalcost', 'crm_price_value', 'Cтоимость',), 'set_price_base'),
        (('housing', 'building', 'дом', 'корпус', 'b', 'building_number',
          'house', 'corpus_label', 'corpus'), 'set_building'),
        (('№ кв', '№ квартиры', 'номер', 'number', 'nt', 'n', 'flat_number', 'num', '№',
          'flatnumber', 'crm_number', 'ApartmentNumber', 'flat_num'), 'set_number'),
        (('numberOnFloor', 'number_on_floor', '№ на этаже', 'flatOnFloor'), 'set_number_on_site'),
        (('section', 'секция', 'парадная', 's', 'section_number', 'entrance',
          'подъезд'), 'set_section'),
        (('жилая площадь', 'площадь комнат', 'жилая', 's комнат', 'livingSquare',
          'area-live', 'жил. площадь', 'area_live', 'area_living', 'жилая пл.',
          'square_living', 'livingArea', 'areaLiving'), 'set_living_area'),
        (('высота потолков', 'ceilingHeight', 'потолки', 'высота потолка', 'потолок'), 'set_ceil'),
        (('этаж', 'floor', 'f', 'floor_number', 'crm_floor', 'floorNumber'), 'set_floor'),
        (('отделка', 'decoration', 'renovation', 'finish', 'has_interior'), 'set_finishing_name'),
        (('цена со скидкой', 'discountprice'), 'set_price_sale'),
        (('imgLink', 'flatPlanImageUrl', 'план', 'img', 'plan', 'pic', 'ImageUrl',
          'image'), 'set_plan'),
        (('количество уровней', 'level', ), 'set_level'),
        (('балкон', 'balcony', 'площадь балкона', 'площадь лоджии', 'balconSquare',
          'crm_balcony_count', 'balconiescount'), 'set_balcon'),
        (('лоджия', 'crm_loggia_count', 'loggiascount'), 'set_loggia'),
        (('терраса', 'площадь террасы'), 'set_terrace'),
        (('вид из окон', 'окна', 'вид', 'view', 'сторона света', 'crm_window_view'), 'set_view'),
    ]

    def __init__(self, restricted_methods=None, restricted_keys=None):
        self._restructure_map()
        # If Mapper find method from restricted, it not set value to obj
        self.restricted_methods = []
        if restricted_methods:
            self.restricted_methods = restricted_methods
        # If Mapper find key from restricted, it not set value to obj
        if restricted_keys:
            self.restricted_keys.extend(restricted_keys)

    def _restructure_map(self):
        # Convert from human view to machine view
        pairs = []
        for method_by_name in self.method_by_names:
            for name in method_by_name[0]:
                pairs.append((name, method_by_name[1]))
        pairs.sort(key=lambda x: len(x[0]), reverse=True)
        self.method_by_names = pairs

    @staticmethod
    def _clean_key(key, exact_match):
        if key and isinstance(key, Tag):
            key = key.get_text(separator=" ").strip()
        key = Utils._normalize_str(key)
        restricted = [',', 'м²', 'м2', 'кв.м.', 'кв.м']
        if exact_match:
            key = Utils.remove_restricted(key, restricted)
        return key

    @staticmethod
    def _clean_value(value):
        if value and isinstance(value, Tag):
            value = value.get_text(separator=" ").strip()
        if isinstance(value, str):
            value = Utils._normalize_str(value)
            restricted = []
            value = Utils.remove_restricted(value, restricted)
        return value

    def map_by_dict(self, obj, dict_, exact_match=False, allowed_methods=None):
        if dict_:
            for key, value in dict_.items():
                self.map_by_one(obj, key, value, exact_match, allowed_methods)

    def map_by_one(self, obj, key, value, exact_match=False,
                   allowed_methods=None, restricted_methods=None):
        if restricted_methods:
            self.restricted_methods.extend(restricted_methods)
        if key:
            key = self._clean_key(key, exact_match)
            if self.restricted_keys and key.lower() in self.restricted_keys:
                return
            value_text = self._clean_value(value)
            if value_text is None or value_text == "":
                return
            # print(repr(key), value_text)
            map_method = self._map_key_to_method(key, exact_match)
            if map_method:
                # print(map_method, repr(key), repr(value_text))
                if (not allowed_methods or map_method in allowed_methods) and\
                        (not self.restricted_methods or map_method not in self.restricted_methods):
                    if not value_text and map_method == 'set_plan':
                        obj.__getattribute__(map_method)(value)
                    else:
                        obj.__getattribute__(map_method)(value_text)

    def map_by_table(self, obj, table, exact_match=False, allowed_methods=None):
        head = None
        rows = table.find_all('tr')
        head = rows[0].find_all('th')
        if not head:
            # this mean table without head, each row have name in td
            # (one td for name other for value)
            for tr in rows:
                info = tr.find_all('td')
                if len(info) != 2:
                    raise Exception('Unexpected table with more then pair name, value', info)
                self.map_by_one(obj, info[0], info[1], exact_match, allowed_methods)
        elif len(head) == 1:
            # This mean structure, where each cell have th and td
            for tr in rows:
                self.map_by_one(obj, tr.th, tr.td, exact_match, allowed_methods)
        else:
            info = tr.find_all('td')
            for key, value in zip(head, info):
                self.map_by_one(obj, key, value, exact_match, allowed_methods)

    def map(self, obj, keys, values, exact_match=False, allowed_methods=None):
        if len(keys) != len(values):
            raise Exception('keys and value have different lenght', len(keys), len(values))
        for key, value in zip(keys, values):
            self.map_by_one(obj, key, value, exact_match, allowed_methods)

    def _map_key_to_method(self, key, exact_match):
        for map_key, map_method in self.method_by_names:
            if exact_match:
                if key.lower() == map_key.lower():
                    return map_method
            else:
                if map_key.lower() in key.lower():
                    return map_method

    def preprocess_table(cls, bs, row_selector='tr'):
        head = None
        for row in bs.select(row_selector):
            if not head:
                head = row
                continue
            yield row, head


class BaseParser:

    def __init__(self, url_base=None, complex_name=None):
        self.url_base = url_base
        self.complex_name = complex_name
        self.loaded_objects = []
        self.preloaded_objects = []
        self.session = requests.Session()
        self.loaded_links = []
        # After need_stop=True no more object will be saved
        self.need_stop = False
        self.mapper = TableMapper()

    def save_JS_obj(self, obj, extract=True):
        if obj and not self.need_stop:
            if not obj.complex:
                obj.complex = self.complex_name
            if extract:
                obj.final_check()
                self.loaded_objects.append(obj.pre_json())
            else:
                self.preloaded_objects.append(obj)

    def convert_do_dict(self, del_same=False):
        converted_object = []
        for obj in self.preloaded_objects:
            if hasattr(obj, 'id'):
                del obj.id
            obj.final_check()
            if not del_same or obj.pre_json() not in converted_object:
                converted_object.append(obj.pre_json())
        self.loaded_objects.extend(converted_object)

    def append_estate_link(self, link):
        if link in self.loaded_links:
            raise Exception('Link, was already loaded', link)
        self.loaded_links.append(link)

    def output_result(self):
        output_list = []
        for element in self.loaded_objects:
            if element not in output_list:
                output_list.append(element)
        print(json.dumps(output_list, cls=DecimalEncoder, indent=1,
                         sort_keys=False))

# Parser Script V1.14
# ___________________________PARSER_UNIQUE_BODY_______________________________________


class Parser(BaseParser):
    def parse_estate(self, art_code):
        with self.session.get(self.url_base, verify=False) as req:
            loaded = req.json()['data']['flats']
            for estate in loaded:
                self.extract_data(loaded[estate])

    def load_data(self):
        art_codes = ['flat', 'parking_underground', 'parking', 'store']
        for art_code in art_codes:
            self.parse_estate(art_code)

    def get_flat_url(self, link):
        return f"https://murinoclub.ru{link}"

    def extract_data(self, data, head=None):
        obj = EstateObject(self.url_base)

        obj.flat_url = self.get_flat_url(data['link'])
        obj.set_obj_type('flat')
        obj.set_plan(self.get_flat_url(data['planBig']))
        obj.set_area(data['area'])
        obj.set_comissioning(data['deadlineText'])
        obj.set_price_base(data['price'])
        if data['isBooked']:
            obj.set_sale_status('Забронировано')
            obj.set_in_sale(1)
        try:
            if data['options'][0]['name'] != 'Без отделки':
                obj.set_finished(1)
        except:
            pass
        obj.set_floor(data['floor'])
        obj.set_rooms(data['type'])
        match = re.search(r"(?<=№)\s*(\d+)", data['title'])
        obj.set_number(match.group(1))

        self.save_JS_obj(obj)


def price():
    parser = Parser(url_base='https://murinoclub.ru/api/estateSearch/',
                    complex_name='Мурино Клаб (Санкт-Петербург)')
    parser.load_data()
    parser.output_result()


if __name__ == "__main__":
    price()
