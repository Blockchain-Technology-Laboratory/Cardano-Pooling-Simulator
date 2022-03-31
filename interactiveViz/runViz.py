# -*- coding: utf-8 -*-
"""
Created on Thu Jun 17 13:51:21 2021

@author: chris
"""
from mesa.visualization.UserParam import UserSettableParameter
from mesa.visualization.ModularVisualization import ModularServer

from logic.sim import Simulation
from logic.helper import MAX_NUM_POOLS

#from myModularVisualization import MyModularServer
from interactiveViz.stackedChartModule import StackedChartModule
from interactiveViz.bubbleChartModule import BubbleChartModule
from interactiveViz.myChartModule import MyChartModule

#todo figure out how to add buttons next to the charts for downloading image upon request or sth similar
poolsChart = MyChartModule([{"label": "#Pools", "title": "Number of pools over time", "xLabel": "Iteration",
                             "yLabel": "Pool count", "tooltipText": " pools", "color": "Blue"}])

poolDynamicsStackedChart = StackedChartModule([{"Label": "PoolSizes", "tooltipText": " Pool", "xLabel": "Iteration",
                                                "yLabel": "Pool size (stake)", "Num_pools": MAX_NUM_POOLS}])

poolScatterChart = BubbleChartModule([{"Label": "StakePairs"}])

pledgeChart = MyChartModule([{"label": "AvgPledge", "title": "Average pledge over time", "xLabel": "Iteration",
                              "yLabel": "Average pledge", "tooltipText": "", "color": "Red"}])

model_params = {
    "n": UserSettableParameter(
        "slider", "Number of stakeholders", 100, 2, 500,
        description="The number of stakeholders in the system."
    ),
    "k": UserSettableParameter(
        "slider", "k", 10, 1, 100,
        description="The k value of the system."
    ),
    "alpha": UserSettableParameter(
        "slider", "α", 0.3, 0, 1, 0.01,
        description="The alpha value of the system."
    ),
    "cost_min": UserSettableParameter(
        "slider", "Minimum cost", 0.001, 0.001, 0.05, 0.001,
        description="The minimum possible cost for operating a stake pool."
    ),
    "cost_max": UserSettableParameter(
        "slider", "Maximum cost", 0.002, 0.002, 0.1, 0.001,
        description="The maximum possible cost for operating a stake pool."
    ),
    "pareto_param": UserSettableParameter(
        "slider", "Pareto shape value", 2, 0.1, 5, 0.1,
        description="The parameter that determines the shape of the distribution that the stake will be sampled from"
    ),
    "relative_utility_threshold": UserSettableParameter(
        "slider", "Relative utility threshold", 0, 0.0, 1, 0.001,
        description="The relative utility increase threshold under which moves are disregarded."
    ),
    "absolute_utility_threshold": UserSettableParameter(
        "slider", "Absolute utility threshold", 1e-9, 0.0, 00.1, 0.0000001,
        description="The absolute utility threshold under which moves are disregarded."
    ),
    "agent_activation_order": UserSettableParameter("choice", "agent activation order",
                                                     value="Random",
                                                     choices=list(Simulation.agent_activation_orders.keys())),
    "seed": UserSettableParameter(
        "number", "Random seed", 42, description="Seed for reproducibility"
    ),
    "min_steps_to_keep_pool": UserSettableParameter(
        "slider", "Min steps to keep pool", 5, 1, 20, 1,
        description="The number of steps for which a agent remains idle after opening a pool."
    ),
    "myopic_fraction": UserSettableParameter(
        "slider", "Myopic fraction", 0, 0.0, 1.0, 0.01,
        description="The fraction of myopic agents in the simulation."
    ),
    "abstention_rate": UserSettableParameter(
        "slider", "Abstaining fraction", 0.1, 0.0, 1.0, 0.01,
        description="The percentage of agents that will abstain from the game in this run."
    ),
    "pool_splitting": UserSettableParameter(
        "checkbox", "Allow pool splitting", False
    ),
    "cost_factor": UserSettableParameter(
        "slider", "Cost factor", 0.4, 0.0, 1, 0.01
    ),
    "max_iterations": UserSettableParameter(
        "slider", "Max iterations", 500, 1, 500, 1,
        description="The maximum number of iterations of the system."
    )
}

# figure out why MyModularServer was not working at some point
# figured out: it only works when I use the ModularServer first so it probably caches some necessary files
server = ModularServer(Simulation,
                       [poolsChart, poolDynamicsStackedChart, poolScatterChart,
                        pledgeChart],
                       "PoS Pooling Games",
                       model_params)

server.port = 8521
server.launch()
