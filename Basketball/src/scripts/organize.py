from organizers import add_features, organize_data, new_games, rankings
import helpers as h

def run():
	year_list = [h.this_season]
	#year_list = range(2011,h.this_season + 1)
	organize_data.run(year_list)
	h.save()
	add_features.run(year_list)
	new_games.get()
	rankings.get()
