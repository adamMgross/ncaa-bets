from scripts import update, predict
from scripts.organize import Organizer
import helpers as h
import sys


def normal(year_list=[h.this_season]):
	update.run()
	org = Organizer(year_list)
	org.run()
	predict.today()


if len(sys.argv) == 1:
	normal()
else:
	arg = sys.argv[1]
	# Rescrapes data from every site for every year
	if arg == 'rescrape':
		if len(sys.argv) < 3:
			update.rescrape()
	# Recreates prediction dataset for every year
	elif arg == 'all':
		normal(h.all_years)
	# Only updates today's data
	elif arg == 'today':
		update.run(today_only=True)
		org = Organizer([h.this_season])
		org.run()
		predict.today()
	# Runs tests
	elif arg == 'test':
		predict.test()
	else:
		print("Argument not found.")
		print("Options: 'today', 'all', 'test', 'rescrape'.")