import helpers as h
import math
import os
import sqlite3
import csv
import numpy as np
from datetime import date,timedelta
import pandas as pd
from scrapers.shared import make_season
import json
import csv

this_season = h.this_season
num_teams = 351
preseason_length = 4
data_path = h.data_path


class Organizer(object):
    def __init__(self, year_list):
        print("Loading games and teams...")
        self.year_list = year_list
        self.main_stats = ["ORtg","DRtg","temp"]
        self.advanced_stats = ["tPAr", "TRBP", "TOVP", "FT"]
        self.dt_columns = ["home", "away", "season", "date", "neutral", "spread", "home_cover",
            "tipstring", "pmargin", "home_winner", "home_big", "away_big", "spread_diff",
            "home_fav", "away_fav", "home_movement", "away_movement", "home_public",
            "away_public", "home_ats", "away_ats", "home_tPAr", "away_tPAr", "home_reb",
            "away_reb", "home_TOVP", "away_TOVP", "home_FT", "away_FT", "key"]
        self.skip_today = ["home_cover", "key"]
        with sqlite3.connect(h.database) as db:
            all_df = self.join_into_df(db)
            self.today = self.join_today_into_df(db).set_index('Game_ID').to_dict('index')
        relevant_df = pd.concat([all_df.ix[all_df['season']==str(year)] for year in year_list]).set_index('key', drop=False)
        self.game_dict = relevant_df.to_dict('index')
        to_drop = []
        for key in list(self.game_dict.keys()):
            if not self.add_vars(self.game_dict[key]):
                del self.game_dict[key]
                to_drop.append(key)
        relevant_df = relevant_df.drop(to_drop)
        self.teams = self.get_teams(relevant_df)
        self.margin_groups = {}
        self.diff_groups = {}
        self.test_data = self.initialize_test_data()


    def create_dt_table(self,cur):
        cur.execute('''DROP TABLE IF EXISTS decision_tree''')
        cur.execute('''CREATE TABLE decision_tree (home TEXT, away TEXT, 
            season TEXT, date TEXT, neutral INTEGER, spread REAL, home_cover INTEGER,
            tipstring TEXT, pmargin INTEGER, home_winner INTEGER, home_big INTEGER,
            away_big INTEGER, spread_diff INTEGER, home_fav INTEGER, away_fav INTEGER,
            home_movement INTEGER, away_movement INTEGER, home_public INTEGER,
            away_public INTEGER, home_ats INTEGER, away_ats INTEGER, home_tPAr INTEGER,
            away_tPAr INTEGER, home_reb INTEGER, away_reb INTEGER, home_TOVP INTEGER,
            away_TOVP INTEGER, home_FT INTEGER, away_FT INTEGER, key TEXT)''')


    def run(self):
        with sqlite3.connect(h.database) as db:
            cur = db.cursor()
            self.add_features(cur)
            self.get_new_games(cur)
            self.get_rankings()
            db.commit()


    def join_into_df(self,db):
        return pd.read_sql_query("""SELECT Game_Home AS home, Game_Away AS away, 
            espn.Season AS season, Game_Date AS date, Game_Tipoff AS tipoff,
            Home_Score, Away_Score, Home_Score - Away_Score AS margin,
            Neutral_Site AS neutral, group_concat(team) AS teams, group_concat(ORtg) AS ORtgs, Pace, 
            group_concat(tPAr) AS tPArs, group_concat(TRBP) AS TRBPs, group_concat(FT) AS FTs,
            group_concat(TOVP) AS TOVPs, open_line, close_line AS spread, home_ats, away_ats, 
            home_side_pct, away_side_pct, Key AS key
            FROM cbbref
            INNER JOIN espn ON espn.Game_ID = cbbref.Game_ID
            LEFT JOIN vegas ON espn.Game_ID = vegas.Game_ID
            GROUP BY espn.Game_ID""", db)


    def add_features(self,cur):
        print("Adding features")
        self.dt_keys = set()
        if self.year_list == h.all_years:
            self.create_dt_table(cur)
        else:
            self.dt_keys = self.get_dt_keys(cur)
        game_date_dict = self.get_game_date_dict()
        self.run_preseason()
        self.old_predictions = []
        for year in self.year_list:
            print(year)
            dates = make_season(year)
            for d in dates:
                if int(d.replace('-','')) >= int(date.today().strftime('%Y%m%d')):
                    break
                elif d in game_date_dict:
                    game_keys = game_date_dict[d]
                else:
                    if d == str(date.today() - timedelta(1)):
                        print("No games from yesterday.")
                    continue
                lg_avg, lg_std = self.get_avg_std(year)
                for key in game_keys:
                    game = self.game_dict[key]
                    home = self.teams[game["home"]+str(game["season"])]
                    away = self.teams[game["away"]+str(game["season"])]
                    if not game["key"] in home["games"] + away["games"]:
                        continue
                    if game["key"] in home["games"] and game["key"] != home["games"][0]:
                        print("GAMES OUT OF ORDER!!!")
                        print(game["key"], home["games"])
                    if game["key"] in away["games"] and game["key"] != away["games"][0]:
                        print("GAMES OUT OF ORDER!!!")
                        print(game["key"], away["games"])

                    predict = {}
                    self.update_stats([home,away])
                    predict["pmargin"] = self.get_pmargin(home,away,game)
                    if game["home_cover"] != 0 and game["key"] not in self.dt_keys and self.make_prediction(predict, game, home, away, lg_avg, lg_std):
                        self.add_gen_info(predict,game)
                        self.old_predictions.append(predict)
                    self.add_test_data(game, predict["pmargin"])
                    self.store_results(home,away,game)
        self.print_test_results()
        self.add_dt_predictions(cur)
        print()


    def get_dt_keys(self,cur):
        cur.execute("""SELECT key FROM decision_tree""")
        return set([t[0] for t in cur.fetchall()])


    def add_dt_predictions(self,cur):
        items = [tuple([p[col] for col in self.dt_columns]) for p in self.old_predictions]
        cur.executemany('''INSERT INTO decision_tree VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''', items)


    def store_results(self, home, away, game):
        for loc,team in [("home",home),("away",away)]:
            for stat in self.advanced_stats:
                team[stat].append(game["{}_{}".format(loc,stat)])
        home["oTOVP"].append(game["away_TOVP"])
        away["oTOVP"].append(game["home_TOVP"])
        home_o = 3 if not game["neutral"] else 0
        away_o = -2 if not game["neutral"] else 0
        home_o_diff = (home["adj_ORtg"][-1] - away["adj_DRtg"][-1]) / 2
        away_o_diff = (away["adj_ORtg"][-1] - home["adj_DRtg"][-1]) / 2
        temp_diff = (home["adj_temp"][-1] - away["adj_temp"][-1]) / 2
        home_results = {}
        home_results["key"] = game["key"]
        home_results["adj_ORtg"] = game["home_ORtg"] + home_o_diff - home_o
        home_results["adj_DRtg"] = game["away_ORtg"] - away_o_diff - away_o
        home_results["adj_temp"] = game["Pace"] + temp_diff
        away_results = {}
        away_results["key"] = game["key"]
        away_results["adj_ORtg"] = game["away_ORtg"] + away_o_diff - away_o
        away_results["adj_DRtg"] = game["home_ORtg"] - home_o_diff - home_o
        away_results["adj_temp"] = game["Pace"] - temp_diff
        if game["key"] in home["games"]:
            home["prev_games"].append(home_results)
            if home["games"][0] == game["key"]:
                del home["games"][0]
        else:
            # Game in this team's preseason
            for stat in self.main_stats:
                del home["adj_" + stat][-1]
        if game["key"] in away["games"]:
            away["prev_games"].append(away_results)
            if away["games"][0] == game["key"]:
                del away["games"][0]
        else:
            # Game in this team's preseason
            for stat in self.main_stats:
                del away["adj_" + stat][-1]


    def add_gen_info(self, predict, game, new_game=False):
        gen_info = ["home", "away", "season", "neutral", "spread", "date"]
        gen_info += self.skip_today if not new_game else []
        for v in gen_info:
            predict[v] = game[v]
        hour = int(game["tipoff"].split(":")[0])
        predict["tipstring"] = "{}:{} {}M ET".format((hour%12 if hour % 12 != 0 else 12),game["tipoff"].split(":")[1],("A" if hour//12 == 0 else "P"))


    def make_prediction(self, predict, game, home, away, lg_avg, lg_std):
        try:
            predict["home_winner"] = 1 if predict["pmargin"] + game["spread"] > 0 else 0
            predict["home_big"] = 1 if game["spread"] <= -10 else 0
            predict["away_big"] = 1 if game["spread"] >= 7 else 0
            predict["spread_diff"] = 1 if abs(predict["pmargin"] + game["spread"]) >= 3 else 0
            predict["home_fav"] = 1 if predict["pmargin"] + game["spread"] >= 2 else 0
            predict["away_fav"] = 1 if predict["pmargin"] + game["spread"] <= -2 else 0
            predict["home_movement"] = 1 if game["line_movement"] <= -1 else 0
            predict["away_movement"] = 1 if game["line_movement"] >= 1 else 0
            predict["home_public"] = 1 if game["home_side_pct"] >= 60 else 0
            predict["away_public"] = 1 if game["home_side_pct"] <= 40 else 0
            predict["home_ats"] = 1 if game["home_ats"] > .55 else 0
            predict["away_ats"] = 1 if game["away_ats"] > .55 else 0
            predict["home_tPAr"] = 1 if np.mean(home["tPAr"]) > lg_avg["tPAr"] + lg_std["tPAr"] / 2 else 0
            predict["away_tPAr"] = 1 if np.mean(away["tPAr"]) > lg_avg["tPAr"] + lg_std["tPAr"] / 2 else 0
            predict["home_FT"] = 1 if np.mean(home["FT"]) > lg_avg["FT"] + lg_std["FT"] / 2 else 0
            predict["away_FT"] = 1 if np.mean(away["FT"]) > lg_avg["FT"] + lg_std["FT"] / 2 else 0
            predict["home_reb"] = 1 if np.mean(home["TRBP"]) > np.mean(away["TRBP"]) + lg_std["TRBP"]/2 else 0
            predict["away_reb"] = 1 if np.mean(away["TRBP"]) > np.mean(home["TRBP"]) + lg_std["TRBP"]/2 else 0
            predict["home_TOVP"] = 1 if np.mean(home["TOVP"]) > lg_avg["TOVP"] and np.mean(away["oTOVP"]) > lg_avg["TOVP"] else 0
            predict["away_TOVP"] = 1 if np.mean(away["TOVP"]) > lg_avg["TOVP"] and np.mean(home["oTOVP"]) > lg_avg["TOVP"] else 0
        except Exception as e:
            print(e)
            return False
        return True


    def get_pmargin(self, home, away, game):
        home_o = 3 if not game["neutral"] else 0
        home_em = home["adj_ORtg"][-1] - home["adj_DRtg"][-1]
        away_em = away["adj_ORtg"][-1] - away["adj_DRtg"][-1]
        tempo = (home["adj_temp"][-1] + away["adj_temp"][-1]) / 2
        em_diff = (4 * home_o + home_em - away_em) / 100
        pmargin = em_diff * tempo * .5
        if pmargin > 0 and pmargin <= 6:
            pmargin += 1
        if pmargin < 0 and pmargin >= -6:
            pmargin -= 1
        pmargin *= .8
        pmargin = 1 if not pmargin else pmargin
        return round(pmargin)


    def print_test_results(self):
        for key in sorted(self.margin_groups.keys()):
            data = {
                "group": key * 5,
                "margmed": np.median(self.margin_groups[key]),
                "diffmed": np.median(self.diff_groups[key]),
                "count": len(self.margin_groups[key])
            }
            print("{group:>5}{margmed:>5}{diffmed:>15}{count:>6}".format(**data))
        print("{:<60}{:.4}".format("Average Difference between Margin and Prediction:",np.mean(self.test_data['pm_diff'])))
        print("{:<60}{:.4}".format("Average Difference between Margin and Spread:",np.mean(self.test_data['s_diff'])))
        print("{:<60}{:.4}".format("Percentage of Picks that are Favorites:",sum(self.test_data['favs']) / self.test_data['all']))
        print("{:<60}{:.4}".format("Win Percentage ATS for Favorites:",sum(self.test_data['fav_wins']) / self.test_data['all']))
        print("{:<60}{:.4}".format("Percentage of Picks ATS that are Home Teams:",sum(self.test_data['home_ats']) / self.test_data['trues']))
        print("{:<60}{:.4}".format("Win Percentage ATS for Home Teams:",sum(self.test_data['home_ats_wins']) / self.test_data['trues']))
        print("{:<60}{:.4}".format("Percentage of Picks that are Home Teams:",sum(self.test_data['homes']) / self.test_data['trues']))
        print("{:<60}{:.4}".format("Percentage of Favorites that are Home Teams:",sum(self.test_data['vegas_homes']) / self.test_data['trues']))
        print("{:<60}{:.4}".format("Win Percentage of Home Teams:",sum(self.test_data['home_wins']) / self.test_data['trues']))
        print("{:<60}{:.4}".format("Win Percentage:",sum(self.test_data['picks']) / self.test_data['all']))
        print("{:<60}{:.4}".format("Vegas Win Percentage:",sum(self.test_data['vegas_picks']) / self.test_data['all']))
        print("{:<60}{:.4}".format("Win Percentage ATS:",sum(self.test_data['picks_ats']) / self.test_data['all']))


    def initialize_test_data(self):
        d = {}
        d['pm_diff'] = []
        d['s_diff'] = []
        d['favs'] = []
        d['fav_wins'] = []
        d['picks'] = []
        d['vegas_picks'] = []
        d['picks_ats'] = []
        d['all'] = 0
        d['home_ats'] = []
        d['home_ats_wins'] = []
        d['homes'] = []
        d['vegas_homes'] = []
        d['home_wins'] = []
        d['trues'] = 0
        return d


    def add_test_data(self, game, pmargin):
        # margin, pmargin, spread, neutral
        if not game['spread'] in [None, '', 0]:
            self.test_data['pm_diff'].append(abs(game['margin'] - pmargin))
            self.test_data['s_diff'].append(abs(game['margin'] + game['spread']))
            # WRONG
            neg_fav = game['spread'] * (game['spread'] + pmargin)
            self.test_data['favs'].append(1 if neg_fav < 0 else 0 if neg_fav > 0 else .5)
            # WRONG
            neg_fav_act = game['spread'] * (game['spread'] + game['margin'])
            self.test_data['fav_wins'].append(1 if neg_fav_act < 0 else 0 if neg_fav_act > 0 else .5)
            self.test_data['picks'].append(1 if game['margin'] / pmargin > 0 else 0)
            self.test_data['vegas_picks'].append(1 if game['spread'] / game['margin'] < 0 else 0)
            if game['margin'] + game['spread'] != 0 and game['spread'] + pmargin != 0:
                self.test_data['picks_ats'].append(1 if (game['margin'] + game['spread']) / (pmargin + game['spread']) > 0 else 0)
            else:
                self.test_data['picks_ats'].append(.5)
            self.test_data['all'] += 1
            if not game['neutral']:
                self.test_data['home_ats'].append(1 if game['spread'] + pmargin > 0 else 0 if game['spread'] + pmargin < 0 else .5)
                self.test_data['home_ats_wins'].append(1 if game['spread'] + game['margin'] > 0 else 0 if game['spread'] + game['margin'] < 0 else .5)
                self.test_data['homes'].append(1 if pmargin > 0 else 0)
                self.test_data['vegas_homes'].append(1 if game['spread'] < 0 else 0 if game['spread'] > 0 else .5)
                self.test_data['home_wins'].append(1 if game['margin'] > 0 else 0)
                self.test_data['trues'] += 1
        group = pmargin // 5
        self.margin_groups[group] = self.margin_groups.get(group, []) + [game['margin']]
        self.diff_groups[group] = self.diff_groups.get(group, []) + [pmargin - game['margin']]


    def update_stats(self, team_list):
        dicts = [{},{}]
        for stat in self.main_stats:
            for index,d in enumerate(dicts):
                d["adj_" + stat] = team_list[index]["pre_adj_" + stat]
                weights = 1
                weight = 1
                for result in team_list[index]["prev_games"]:
                    weight *= 1.15
                    d["adj_" + stat] += result["adj_" + stat] * weight
                    weights += weight
                if len(team_list[index]["prev_games"]) > 0:
                    d["adj_" + stat] /= weights
        for index,d in enumerate(dicts):
            for key,value in d.items():
                team_list[index][key].append(value)


    def get_avg_std(self, year):
        avg = {}
        std = {}
        season_teams = [team for key, team in self.teams.items() if team["year"] == year]
        for v in self.advanced_stats:
            stats = [np.mean(team[v]) for team in season_teams]
            avg[v] = np.mean(stats)
            std[v] = np.std(stats)
        return (avg, std)


    # Gets starting stats for a team in the year, these games will not be predicted
    def run_preseason(self):
        self.preseason_averages()
        self.level_off_stats(5)
        self.remove_preseason_games()        

        
    def remove_preseason_games(self):
        for key,team in self.teams.items():
            team["prev_games"] = []
            game_count = min(preseason_length, len(team["games"]))
            for i in range(game_count):
                del team["games"][0]


    def level_off_stats(self,loops):
        for j in range(loops):
            for key,team in self.teams.items():
                game_count = min(preseason_length, len(team["games"]))
                o = 0
                d = 0
                t = 0
                for i in range(game_count):
                    game = self.game_dict[team["games"][i]]
                    home = self.teams[game["home"]+str(game["season"])]
                    away = self.teams[game["away"]+str(game["season"])]
                    # Home court advantage values taken into account only if true home game
                    home_o = 3 if not game["neutral"] else 0
                    away_o = 2 if not game["neutral"] else 0
                    home_o_diff = (home["pre_adj_ORtg"] - away["pre_adj_DRtg"]) / 2
                    away_o_diff = (away["pre_adj_ORtg"] - home["pre_adj_DRtg"]) / 2
                    temp_diff = (home["pre_adj_temp"] - away["pre_adj_temp"]) / 2
                    # Best predictor of Ratings and Pace is an average of the two, so must reverse calculate a team's adjusted rating for the game
                    if game["home"] == team["name"]:
                        o += (game["home_ORtg"] + home_o_diff - home_o) / game_count
                        d += (game["away_ORtg"] - away_o_diff + away_o) / game_count # Positive DRtg good, amount of points fewer they gave up than expected
                        t += (game["Pace"] + temp_diff) / game_count
                    else:
                        o += (game["away_ORtg"] + away_o_diff + away_o) / game_count
                        d += (game["home_ORtg"] - home_o_diff - home_o) / game_count # Positive DRtg good, amount of points fewer they gave up than expected
                        t += (game["Pace"] - temp_diff) / game_count
                team["adj_ORtg"] = [o]
                team["adj_DRtg"] = [d]
                team["adj_temp"] = [t]
            for key,team in self.teams.items():
                try:
                    team["pre_adj_ORtg"] = team["adj_ORtg"][-1]
                    team["pre_adj_DRtg"] = team["adj_DRtg"][-1]
                    team["pre_adj_temp"] = team["adj_temp"][-1]
                except:
                    pass


    def preseason_averages(self):
        for key,team in self.teams.items():
            game_count = min(len(team["games"]), preseason_length)
            if game_count < preseason_length:
                print("{} doesn't have {} games in ".format(team["name"],preseason_length)+key[-4:])
            if not game_count:
                continue
            for i in range(game_count):
                game = self.game_dict[team["games"][i]]
                isHome = game["home"] == team["name"]
                tm = "home" if isHome else "away"
                opp = "away" if isHome else "home"
                team["pre_adj_temp"] = team.get("pre_adj_temp",0) + game["Pace"] / game_count
                team["pre_adj_ORtg"] = team.get("pre_adj_ORtg",0) + game[tm + "_ORtg"] / game_count
                team["pre_adj_DRtg"] = team.get("pre_adj_DRtg",0) + game[opp + "_ORtg"] / game_count
                for stat in self.advanced_stats:
                    team[stat] = team.get(stat,[]) + [game["{}_{}".format(tm,stat)]]
                team["oTOVP"] = team.get("oTOVP",[]) + [game[opp + "_TOVP"]]
            # Averages will be initial stats before we level them off
            team["adj_ORtg"] = [team["pre_adj_ORtg"]]
            team["adj_DRtg"] = [team["pre_adj_DRtg"]]
            team["adj_temp"] = [team["pre_adj_temp"]]


    # Creates game dictionary that facilitates getting games played on the same date
    def get_game_date_dict(self):
        game_date_dict = {}
        for key,game in self.game_dict.items():
            try:
                if game["key"] not in game_date_dict[game["date"]]:
                    game_date_dict[game["date"]].append(game["key"])
            except:
                game_date_dict[game["date"]] = [game["key"]]
        return game_date_dict


    def add_vars(self, game, new_game = False):
        if not new_game:
            game["home_cover"] = 0
            if not self.add_cbbref_vars(game):
                return False
        self.add_vegas_vars(game, new_game)
        game["neutral"] = game["neutral"] in ["True", "1"]
        return True


    def add_cbbref_vars(self, game):
        c_vars = ["team", "ORtg"] + self.advanced_stats
        zipped = [tuple(game[v + "s"].split(',')) for v in c_vars]
        try:
            team1, team2 = zip(*zipped)
            for team in [list(team1),list(team2)]:
                loc = "home_" if team[0] == game['home'] else "away_"
                for index, v in enumerate(team[1:]):
                    key = c_vars[index+1]
                    game[loc + key] = float(v)
        except:
            if len(zipped[0]) == 1:
                return False
            print(game['date'],zipped[0])
        return True


    def add_vegas_vars(self, game, new_game):
        if game["spread"] != None and game["spread"] != "":
            noOpenLine = game["open_line"] != 0 and not game["open_line"]
            if abs(game["spread"]) > 65:
                game["spread"] = 0
            if noOpenLine:
                game["open_line"] = game["spread"]
        elif game["spread"] == None or game["spread"] == "":
            if game["open_line"]:
                game["spread"] = game["open_line"]
            else:
                return
        if not new_game:
            spread_diff = game["spread"] + game["margin"]
            game["home_cover"] = 1 if spread_diff > 0 else -1 if spread_diff < 0 else 0
        game["line_movement"] = game["spread"] - game["open_line"]
        home_ats = game["home_ats"].split("-")
        away_ats = game["away_ats"].split("-")
        game["home_ats"] = .5 if home_ats[0] == "0" and home_ats[1] == "0" else int(home_ats[0]) / (int(home_ats[0])+int(home_ats[1]))
        game["away_ats"] = .5 if away_ats[0] == "0" and away_ats[1] == "0" else int(away_ats[0]) / (int(away_ats[0])+int(away_ats[1]))
        try:
            game["home_side_pct"] = int(game["home_side_pct"])
            game["away_side_pct"] = int(game["away_side_pct"])
        except:
            game["home_side_pct"] = 50
            game["away_side_pct"] = 50


    def get_teams(self, df):
        names_dict = h.read_names()
        teams = {}
        nameset = set(names_dict.values())
        new_teams = ["Grand Canyon", "UMass Lowell", "New Orleans", "Incarnate Word",
                    "Abilene Christian", "Northern Kentucky", "Omaha"]
        for name in nameset:
            team_df = df[(df['home'] == name) | (df['away'] == name)]
            for i in self.year_list:
                if i <= 2013 and name in new_teams:
                    if i == 2013 and name in ["New Orleans", "Northern Kentucky", "Omaha"]:
                        pass
                    else:
                        continue
                key = name + str(i)
                teams[key] = {}
                teams[key]["name"] = name
                teams[key]["year"] = i
                year_df = team_df[(team_df['season'] == str(i))]
                teams[key]["games"] = [index for index, row in year_df.iterrows()]
        return teams


    def join_today_into_df(self,db):
        return pd.read_sql_query("""SELECT espn_today.Game_ID AS Game_ID, Game_Home AS home, 
            espn_today.Season AS season, Game_Away AS away, Game_Date AS date, Game_Tipoff AS tipoff,
            Neutral_Site AS neutral, open_line, close_line AS spread, home_ats, away_ats, home_side_pct, 
            away_side_pct
            FROM espn_today
            INNER JOIN vegas_today ON espn_today.Game_ID = vegas_today.Game_ID""", db)


    def create_dt_today(self,cur):
        cur.execute('''DROP TABLE IF EXISTS decision_tree_today''')
        cur.execute('''CREATE TABLE decision_tree_today (home TEXT, away TEXT, 
            season TEXT, date TEXT, neutral INTEGER, spread REAL,
            tipstring TEXT, pmargin INTEGER, home_winner INTEGER, home_big INTEGER,
            away_big INTEGER, spread_diff INTEGER, home_fav INTEGER, away_fav INTEGER,
            home_movement INTEGER, away_movement INTEGER, home_public INTEGER,
            away_public INTEGER, home_ats INTEGER, away_ats INTEGER, home_tPAr INTEGER,
            away_tPAr INTEGER, home_reb INTEGER, away_reb INTEGER, home_TOVP INTEGER,
            away_TOVP INTEGER, home_FT INTEGER, away_FT INTEGER)''')


    def add_dt_today_predictions(self,cur):
        items = [tuple([p[col] for col in self.dt_columns if col not in self.skip_today]) for p in self.new_predictions]
        cur.executemany('''INSERT INTO decision_tree_today VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''', items)


    def get_new_games(self,cur):
        print("Getting new games")
        self.create_dt_today(cur)
        self.new_predictions = []
        lg_avg, lg_std = self.get_avg_std(this_season)
        for key, game in self.today.items():
            self.add_vars(game,new_game=True)
            home = self.teams[game["home"]+str(this_season)]
            away = self.teams[game["away"]+str(this_season)]
            predict = {}
            self.update_stats([home,away])
            predict["pmargin"] = self.get_pmargin(home,away,game)
            self.make_prediction(predict,game,home,away,lg_avg,lg_std)
            self.add_gen_info(predict,game,new_game=True)
            self.new_predictions.append(predict)
            #print("Found:",game["home"],game["away"])
        if self.new_predictions:
            self.add_dt_today_predictions(cur)
        print()


    def get_rankings(self, year=this_season):
        print("Updating Rankings for {}".format(year))
        rank_path = os.path.join(data_path,'rankings','{}.csv'.format(year))
        em_list = [((team["adj_ORtg"][-1] - team["adj_DRtg"][-1])/2, key) for key, team in self.teams.items() if team["year"] == year]
        rank_list = []
        for rank, em_key in enumerate(sorted(em_list, reverse=True)):
            em, key = em_key
            name = self.teams[key]["name"]
            ortg = round((self.teams[key]["adj_ORtg"][-1] + 100) / 2, 2)
            drtg = round((self.teams[key]["adj_DRtg"][-1] + 100) / 2, 2)
            info = [name, em, ortg, drtg]
            rank_list.append((rank+1,info))
        rankdf = pd.DataFrame.from_items(rank_list,columns=["Name","EM","ORtg","DRtg"],orient="index")
        rankdf.index.name = "Rank"
        rankdf.to_csv(rank_path)
