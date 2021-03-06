# -*- coding: utf-8 -*-
import json
import os
from json import JSONDecodeError

import re
import requests

from easytrader.log import log
from easytrader.webtrader import NotLoginError, TradeError
from easytrader.webtrader import WebTrader
import easytrader.xqtrader


class XueQiuClient(WebTrader):
    config_path = os.path.dirname(easytrader.xqtrader.__file__) + '/config/xq.json'

    def __init__(self, **kwargs):
        super(XueQiuClient, self).__init__()
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 6.1; WOW64; rv:32.0) Gecko/20100101 Firefox/32.0',
            'Host': 'xueqiu.com',
            'Pragma': 'no-cache',
            'Connection': 'keep-alive',
            'Accept': '*/*',
            'Accept-Encoding': 'gzip,deflate,sdch',
            'Cache-Control': 'no-cache',
            'Referer': 'http://xueqiu.com/P/ZH003694',
            'X-Requested-With': 'XMLHttpRequest',
            'Accept-Language': 'zh-CN,zh;q=0.8'
        }
        self.session = requests.Session()
        self.session.headers.update(headers)
        self.account_config = None
        self.config.update({
            "create_cubes_url": "https://xueqiu.com/cubes/create.json",
            "get_token_url": "https://xueqiu.com/service/csrf",
            "get_cubes_list": "https://xueqiu.com/v4/stock/portfolio/stocks.json",
            "get_cubes_detail": "https://xueqiu.com/cubes/quote.json",
        })

    def autologin(self, **kwargs):
        """
        重写自动登录方法
        避免重试导致的帐号封停
        :return:
        """
        self.login()

    def login(self, throw=False):
        """
        登录
        :param throw:
        :return:
        """
        login_status, result = self.post_login_data()
        if login_status is False and throw:
            raise NotLoginError(result)
        log.debug('login status: %s' % result)
        return login_status

    def _prepare_account(self, user='', password='', **kwargs):
        """
        转换参数到登录所需的字典格式
        :param user: 雪球邮箱(邮箱手机二选一)
        :param password: 雪球密码
        :param account: 雪球手机号(邮箱手机二选一)
        :param portfolio_market: 交易市场， 可选['cn', 'us', 'hk'] 默认 'cn'
        :return:
        """
        if 'portfolio_market' not in kwargs:
            kwargs['portfolio_market'] = 'cn'
        if 'account' not in kwargs:
            kwargs['account'] = ''
        self.account_config = {
            'username': user,
            'account': kwargs['account'],
            'password': password,
            'portfolio_market': kwargs['portfolio_market']
        }

    def post_login_data(self):
        login_post_data = {
            'username': self.account_config.get('username', ''),
            'areacode': '86',
            'telephone': self.account_config['account'],
            'remember_me': '0',
            'password': self.account_config['password']
        }
        login_response = self.session.post(self.config['login_api'], data=login_post_data)
        login_status = login_response.json()
        if 'error_description' in login_status:
            return False, login_status['error_description']
        return True, "SUCCESS"

    def __search_stock_info(self, code):
        """
        通过雪球的接口获取股票详细信息
        :param code: 股票代码 000001

        :return: 查询到的股票
        {"code":"SZ000651","name":"格力电器","enName":null,"hasexist":null,"flag":1,"type":null,"current":45.46,"chg":0.6,"percent":"1.34","stock_id":1001306,"ind_id":100007,"ind_name":"家用电器","ind_color":"#82b952","textname":"格力电器(SZ000651)","segment_name":"家用电器","weight":30,"url":"/S/SZ000651","proactive":true,"price":"45.46"}
        ** flag : 未上市(0)、正常(1)、停牌(2)、涨跌停(3)、退市(4)
        """
        data = {
            'code': str(code),
            'size': '300',
            'key': '47bce5c74f',
            'market': self.account_config['portfolio_market'],
        }
        r = self.session.get(self.config['search_stock_url'], params=data)
        stocks = json.loads(r.text)
        stocks = stocks['stocks']
        stock = None
        if len(stocks) > 0:
            stock = stocks[0]
        return stock

    def __get_create_cube_token(self):
        """获取创建组合时需要的token信息
        :return: token
        """
        response = self.session.get(self.config["get_token_url"], params={
            "api": "/cubes/create.json"
        })
        try:
            token_response = json.loads(response.text)
        except JSONDecodeError:
            raise TradeError("解析创建组合的token信息失败: %s" % response.text)
        if 'token' not in token_response:
            raise TradeError("获取创建组合的token信息失败: %s" % response.text)
        return token_response['token']

    @staticmethod
    def get_cube_name(cube_prefix, stock_code):
        return "%s%s" % (cube_prefix, stock_code)

    def create_cube(self, stock_code, weight, cube_prefix="SC", description="", market='cn'):
        """创建组合, 并设置股票初始买入的百分比
        ** 组合名称默认格式为前缀 + 股票代码
        :param stock_code: str 股票代码
        :param weight: int 初始仓位的百分比， 0 - 100 之间的整数
        :param cube_prefix: 组合名字的前缀
        :param description: 组合描述信息
        :param market: 市场范围, cn: 沪深, us: 美股, hk: 港股
        :return: (是否创建成功, 组合代码, 组合名称)
        """
        cube_name = self.get_cube_name(cube_prefix, stock_code)
        stock = self.__search_stock_info(stock_code)
        if stock is None:
            raise TradeError(u"没有查询要操作的股票信息")
        if stock['flag'] != 1:
            raise TradeError(u"未上市、停牌、涨跌停、退市的股票无法操作。")
        holdings = [
            {
                "code": stock['code'],
                "name": stock['name'],
                "enName": stock['enName'],
                "hasexist": stock['hasexist'],
                "flag": stock['flag'],
                "type": stock['type'],
                "current": stock['current'],
                "chg": stock['chg'],
                "percent": str(stock['percent']),
                "stock_id": stock['stock_id'],
                "ind_id": stock['ind_id'],
                "ind_name": stock['ind_name'],
                "ind_color": stock['ind_color'],
                "textname": "%s(%s)" % (stock['name'], stock['code']),
                "segment_name": stock['ind_name'],
                "weight": weight,
                "url": "/S/" + stock['code'],
                "proactive": True,
                "price": str(stock['current'])
            }
        ]

        create_cube_data = {
            "name": cube_name,
            "cash": 100 - weight,
            "description": description,
            "market": market,
            "holdings": json.dumps(holdings),
            "session_token": self.__get_create_cube_token(),
        }
        try:
            cube_res = self.session.post(self.config['create_cubes_url'], data=create_cube_data)
        except Exception as e:
            log.warn('创建组合%s失败: %s ' % (cube_name, e))
            return (False, None, None)
        else:
            log.debug('创建组合%s: 持仓比例%d' % (cube_name, weight))
            cube_res_status = json.loads(cube_res.text)
            if 'error_description' in cube_res_status.keys() and cube_res.status_code != 200:
                log.error('创建组合错误: %s, error_no: %s, error_info: %s' % (
                    cube_res_status['error_description'],
                    cube_res_status['error_code'],
                    cube_res_status['error_description'],
                ))
                if cube_res_status['error_code'] == '20912':
                    log.error("组合名称: %s 不符合要求, 请尝试换一个组合名称，组合名称只能是中文，英文，数字(测试时发现某些情况下可以包含下划线)" % cube_name)
                return (False, None, None)
            log.debug('创建组合成功 %s: 持仓比例%d, 创建信息: \n%s' % (
                cube_name, weight, json.dumps(cube_res_status, ensure_ascii=False, indent=4)))
            return (True, cube_res_status['symbol'], cube_name)

    def get_cubes_list(self, type=4):
        """获取组合详情，默认获取自选组合
        :param type: 组合名字的前缀, 1: 全部组合。 4: 我的组合。 5: 只看沪深组合。 6: 只看美股。7: 只看港股
        :return: 组合列表
        """
        response = self.session.get(self.config['get_cubes_list'], params={
            "category": 1,
            "type": type
        })
        try:
            cubes_response = json.loads(response.text)
        except JSONDecodeError:
            log.warning(response.text)
            raise TradeError("解析组合列表失败: %s" % response.text)
        if 'stocks' not in cubes_response:
            log.warning(cubes_response)
            raise TradeError("获取组合信息失败: %s" % response.text)
        cubes_code_list = [cube['code'] for cube in cubes_response['stocks']]

        response = self.session.get(self.config['get_cubes_detail'], params={
            "code": ','.join(cubes_code_list),
            "return_hasexist": False,
        })
        try:
            cubes_detail_response = json.loads(response.text)
        except JSONDecodeError:
            raise TradeError("解析组合详情失败: %s" % response.text)
        if 'stocks' not in cubes_response:
            raise TradeError("获取组合信息失败: %s" % response.text)
        return cubes_detail_response

    def get_portfolio_info(self, portfolio_code):
        """
        获取组合信息
        :return: 字典
        """
        url = self.config['portfolio_url'] + portfolio_code
        html = self.__get_html(url)
        match_info = re.search(r'(?<=SNB.cubeInfo = ).*(?=;\n)', html)
        if match_info is None:
            raise Exception('cant get portfolio info, portfolio html : {}'.format(html))
        try:
            portfolio_info = json.loads(match_info.group())
        except Exception as e:
            raise Exception('get portfolio info error: {}'.format(e))
        return portfolio_info

    def __get_html(self, url):
        return self.session.get(url).text

if __name__ == '__main__':
    from backtradercn.settings import settings as conf

    client = XueQiuClient()
    client.prepare(account=conf.XQ_ACCOUNT, password=conf.XQ_PASSWORD, portfolio_market=conf.XQ_PORTFOLIO_MARKET)
    # 创建股票代码为000651的组合
    response = client.create_cube(stock_code="000651", weight=5, cube_prefix=conf.XQ_CUBES_PREFIX)
    print(response)
    # 获取自定义组合信息
    cubes_list = client.get_cubes_list()
    print(cubes_list)
