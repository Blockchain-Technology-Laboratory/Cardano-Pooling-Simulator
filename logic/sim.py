# -*- coding: utf-8 -*-

import csv
import time
import pathlib
import math
import collections
import sys
import random
import pickle as pkl
import numpy as np
from mesa import Model
from mesa.datacollection import DataCollector
from mesa.time import BaseScheduler, SimultaneousActivation, RandomActivation

from logic.stakeholder import Stakeholder
from logic.activations import SemiSimultaneousActivation
import logic.helper as hlp
import logic.model_reporters as reporters

class Simulation(Model):
    """
    Simulation of staking behaviour in Proof-of-Stake Blockchains.
    """

    agent_activation_orders = {
        "Random": RandomActivation,
        "Sequential": BaseScheduler,
        "Simultaneous": SimultaneousActivation, # note that during simultaneous activation agents apply their moves sequentially which may not be the expected behaviour
        "Semisimultaneous": SemiSimultaneousActivation

    }

    #todo split into two classes? simulation (with max_iterations, steps_for_convergence etc) and system (with k, alpha etc)
    def __init__(self, n=1000, k=100, alpha=0.3, stake_distr_source='Pareto', myopic_fraction=0, abstention_rate=0,
                 abstention_known=False, relative_utility_threshold=0, absolute_utility_threshold=1e-9,
                 min_steps_to_keep_pool=5, pool_splitting=True, seed=None, pareto_param=2.0, max_iterations=1000,
                 cost_min=1e-4, cost_max=1e-3, cost_factor=0.7, agent_activation_order="Random", total_stake=-1,
                 steps_for_convergence=10, extra_cost_type='fixed_fraction', reward_function_option=0, execution_id='',
                 seq_id=-1, parent_dir='', metrics=None, generate_graphs=True, input_from_file=False):
        # todo make sure that the input is valid? n > 0, 0 < k <= n
        if input_from_file:
            args = hlp.read_args_from_file("args.json")
        else:
            args = {}
            args.update(locals()) # keep all input arguments in a dictionary
            args.pop('self')
            args.pop('__class__')
            args.pop('input_from_file')
            args.pop('args')
        if args['metrics'] is None:
            args['metrics'] = [1, 2, 3]

        seed = args['seed']
        if seed is None or seed == 'None':
            seed = random.randint(0, 9999999)
        seed = str(seed)  # to maintain consistency among seeds, because command line arguments are parsed as strings
        super().__init__(seed=seed)

        seq_id = args['seq_id']
        if seq_id == -1:
            seq_id = hlp.read_seq_id() + 1
            hlp.write_seq_id(seq_id)
        self.seq_id = seq_id

        execution_id = args['execution_id']
        if execution_id == '' or execution_id == 'temp':
            # No identifier was provided by the user, so we construct one based on the simulation's parameter values
            execution_id = hlp.generate_execution_id(args)
        execution_id = str(seq_id) + '-' + execution_id + '-seed-' + seed
        self.execution_id = execution_id

        path = pathlib.Path.cwd() / "output" / args['parent_dir'] / self.execution_id
        pathlib.Path(path).mkdir(parents=True)
        self.directory = path
        self.export_args_file(args)

        # An era is defined as a time period during which the parameters of the model don't change
        self.current_era = 0
        total_eras = 1

        extra_fields = ['n', 'k', 'alpha', 'myopic_fraction', 'relative_utility_threshold', 'absolute_utility_threshold',
                 'min_steps_to_keep_pool', 'pool_splitting', 'max_iterations', 'cost_factor', 'agent_activation_order',
                  'extra_cost_type', 'reward_function_option', 'generate_graphs']
        adjustable_params = {} #todo define which args should not be saved as adjustable params (e.g. abstention rate)
        for field in extra_fields:
            value = args[field]
            if isinstance(value, list):
                # a number of values were given for this field, to be used in different eras
                adjustable_params[field] = value # todo use int for k?
                setattr(self, field, value[self.current_era])
                if len(value) > total_eras:
                    total_eras = len(value)
            else:
                setattr(self, field, value)

        self.total_eras = total_eras
        self.adjustable_params = adjustable_params
        self.k = int(self.k)

        if args['abstention_known']:
            # The system is aware of the abstention rate of the system, so it inflates k (and subsequently lowers beta)
            # to make it possible to end up with the original desired number of pools
            self.k = int(self.k / (1 - args['abstention_rate']))

        self.running = True  # for batch running and visualisation purposes
        self.schedule = self.agent_activation_orders[self.agent_activation_order](self)

        total_stake = self.initialize_agents(args['cost_min'], args['cost_max'], args['pareto_param'], args['stake_distr_source'].lower(),
                                             imposed_total_stake=args['total_stake'], seed=seed)
        self.total_stake = total_stake / (1 - args['abstention_rate'])
        print("Total stake (including abstaining fraction): ", self.total_stake)

        if self.total_stake <= 0:
            raise ValueError('Total stake must be > 0')
        self.perceived_active_stake = self.total_stake
        self.beta = self.total_stake / self.k
        self.export_input_desc_file(seed)

        self.consecutive_idle_steps = 0  # steps towards convergence
        self.current_step_idle = True
        self.min_consecutive_idle_steps_for_convergence = max(min_steps_to_keep_pool + 1, args['steps_for_convergence'])
        self.pools = dict()
        self.revision_frequency = 10  # defines how often active stake and expected #pools are revised #todo read from json file?
        self.initialise_pool_id_seq()  # initialise pool id sequence for the new model run

        # metrics to track at every step of the simulation
        model_reporters = {
            reporters.reporter_ids[reporter_id]: reporters.all_model_reporters[reporters.reporter_ids[reporter_id]] for
            reporter_id in args['metrics']}
        self.datacollector = DataCollector(model_reporters=model_reporters)

        self.start_time = time.time()
        self.equilibrium_steps = []
        self.pivot_steps = []

    def initialize_agents(self, cost_min, cost_max, pareto_param, stake_distr_source, imposed_total_stake, seed):
        if stake_distr_source == 'file':
            stake_distribution = hlp.read_stake_distr_from_file(num_agents=self.n)
        elif stake_distr_source == 'pareto':
            # Allocate stake to the agents, sampling from a Pareto distribution
            stake_distribution = hlp.generate_stake_distr_pareto(num_agents=self.n, pareto_param=pareto_param, seed=seed,
                                                                 total_stake=imposed_total_stake)#, truncation_factor=self.k)
        elif stake_distr_source == 'flat':
            # Distribute the total stake of the system evenly to all agents
            stake_distribution = hlp.generate_stake_distr_flat(num_agents=self.n, total_stake=self.n)#max(imposed_total_stake, 1))
        elif stake_distr_source == 'disparate':
            stake_distribution = hlp.generate_sake_distr_disparity(n=self.n)
        else:
            raise ValueError("Unsupported stake distribution source '{}'.".format(stake_distr_source))
        total_stake = sum(stake_distribution)
        print("Total stake: ", total_stake)
        print("Max stake: ", max(stake_distribution))

        # Allocate cost to the agents, sampling from a uniform distribution
        #todo make cost distr configurable? allow reading from file maybe?
        cost_distribution = hlp.generate_cost_distr_unfrm(num_agents=self.n, low=cost_min, high=cost_max, seed=seed)
        #cost_distribution = hlp.generate_cost_distr_bands(num_agents=self.n, low=cost_min, high=cost_max, num_bands=1)
        #cost_distribution = hlp.generate_cost_distr_nrm(num_agents=self.n, low=cost_min, high=cost_max, mean=5e-6, stddev=5e-1)
        #cost_distribution = hlp.generate_cost_distr_bands_manual(num_agents=self.n, low=cost_min, high=cost_max, num_bands=1)

        num_myopic_agents = int(self.myopic_fraction * self.n)
        unique_ids = [i for i in range(self.n)]
        self.random.shuffle(unique_ids)
        # Create agents
        for i, unique_id in enumerate(unique_ids):
            agent = Stakeholder(
                unique_id=unique_id,
                model=self,
                is_abstainer=False,
                is_myopic=(i < num_myopic_agents),
                cost=cost_distribution[i],
                stake=stake_distribution[i]
            )
            self.schedule.add(agent)
        return total_stake

    # todo use itertools instead (next)
    def initialise_pool_id_seq(self):
        self.id_seq = 0

    def get_next_pool_id(self):
        self.id_seq += 1
        return self.id_seq

    def rewind_pool_id_seq(self, step=1):
        self.id_seq -= step

    def step(self):
        """
        Execute one step of the simulation
        """
        self.get_status()
        self.datacollector.collect(self)

        current_step = self.schedule.steps
        if current_step >= self.max_iterations:
            self.wrap_up_execution()
            return
        if current_step % self.revision_frequency == 0 and current_step > 0:
            self.revise_beliefs()

        # Activate all agents (in the order specified by self.schedule) to perform all their actions for one time step
        self.schedule.step()
        if self.current_step_idle:
            self.consecutive_idle_steps += 1
            if self.has_converged():
                self.equilibrium_steps.append(current_step - self.min_consecutive_idle_steps_for_convergence + 1)
                if self.current_era < self.total_eras - 1:
                    self.adjust_params()
                else:
                    self.wrap_up_execution()
                    return
        else:
            self.consecutive_idle_steps = 0
        self.current_step_idle = True

    def run_model(self):
        """
        Execute multiple steps of the simulation, until it converges or a maximum number of iterations is reached
        :return:
        """
        self.start_time = time.time()
        self.initialise_pool_id_seq()  # initialise pool id sequence for the new model run
        while self.schedule.steps <= self.max_iterations and self.running:
            self.step()

    def has_converged(self):
        """
        Check whether the system has reached a state of equilibrium,
        where no agent wants to change their strategy
        """
        return self.consecutive_idle_steps >= self.min_consecutive_idle_steps_for_convergence

    def export_args_file(self, args):
        filename = 'args.json'
        filepath = self.directory / filename
        hlp.export_json_file(args, filepath)

    def export_input_desc_file(self, seed):
        # generate file that describes the state of the system at step 0
        descriptors = {
            'Randomness seed': seed,
            'Total stake': self.total_stake,
            'Active stake': reporters.get_active_stake_agents(self),
            'Nakamoto coefficient prior': reporters.get_nakamoto_coefficient(self),
            'Cost efficient agents': reporters.get_cost_efficient_count(self)
        }
        filename = "input-descriptors.json"
        filepath = self.directory / filename
        hlp.export_json_file(descriptors, filepath)


    def export_agents_file(self):
        row_list = [["Agent id", "Stake", "Cost", "Potential Profit","Status", "Pools owned"]]
        agents = self.get_agents_dict()
        decimals = 15
        row_list.extend([
            [agent_id, round(agents[agent_id].stake, decimals), round(agents[agent_id].cost, decimals),
             round(hlp.calculate_potential_profit(agents[agent_id].stake, agents[agent_id].cost, self.alpha, self.beta, self.reward_function_option, self.total_stake), decimals),
             "Abstainer" if agents[agent_id].strategy is None else "Operator" if len(agents[agent_id].strategy.owned_pools) > 0 else "Delegator",
             0 if agents[agent_id].strategy is None else len(agents[agent_id].strategy.owned_pools)
             ] for agent_id in range(len(agents))
        ])

        prefix = 'final_configuration_stakeholders-' if self.has_converged() else 'intermediate_configuration_stakeholders-'
        filename = prefix + self.execution_id + '.csv'
        filepath = self.directory / filename
        hlp.export_csv_file(row_list, filepath)
        
    def export_pools_file(self):
        row_list = [["Pool id", "Owner id", "Owner stake", "Pool Pledge", "Pool stake", "Owner cost", "Pool cost", "Pool margin"]]
        agents = self.get_agents_dict()
        pools = self.get_pools_list()
        decimals = 15
        row_list.extend(
            [[pool.id, pool.owner, round(agents[pool.owner].stake, decimals), round(pool.pledge, decimals),
              round(pool.stake, decimals), round(agents[pool.owner].cost, decimals), round(pool.cost, decimals),
              round(pool.margin, decimals)] for pool in pools])
        prefix = 'final_configuration_pools-' if self.has_converged() else 'intermediate_configuration_pools-'
        filename = prefix + self.execution_id + '.csv'
        filepath = self.directory / filename
        hlp.export_csv_file(row_list, filepath)

    def export_metrics_file(self):
        df = self.datacollector.get_model_vars_dataframe()
        filename = 'metrics-' + self.execution_id + '.csv'
        filepath = self.directory / filename
        df.to_csv(filepath, index_label='Round')

    def save_model_state_pkl(self):
        filename = "simulation-object-" + self.execution_id + ".pkl"
        pickled_simulation_filepath = self.directory / filename
        with open(pickled_simulation_filepath, "wb") as pkl_file:
            pkl.dump(self, pkl_file)

    def export_graphs(self):
        figures_dir = self.directory / "figures"
        pathlib.Path(figures_dir).mkdir(parents=True, exist_ok=True)

        rng = np.random.default_rng(seed=156)
        random_colours = rng.random((len(reporters.all_model_reporters), 3))
        all_reporter_colours = dict(zip(reporters.all_model_reporters.keys(), random_colours))

        df = self.datacollector.get_model_vars_dataframe()
        for col in df.columns:
            if isinstance(df[col][0], list):
                hlp.plot_stack_area_chart(pool_sizes_by_step=df[col], execution_id=self.execution_id, path=figures_dir)
            else:
                hlp.plot_line(data=df[col], execution_id=self.execution_id, color=all_reporter_colours[col], title=col, x_label="Round",
                          y_label=col, filename=col, equilibrium_steps=self.equilibrium_steps, pivot_steps=self.pivot_steps,
                          path=figures_dir, show_equilibrium=True)

    def get_pools_list(self):
        return list(self.pools.values())

    def get_agents_dict(self):
        return {agent.unique_id: agent for agent in self.schedule.agents}

    def get_agents_list(self):
        return self.schedule.agents

    def get_status(self):
        print("Step {}: {} pools"
              .format(self.schedule.steps, len(self.pools)))

    def revise_beliefs(self):
        """
        Revise the perceived active stake and expected number of pools,
        to reflect the current state of the system
        The value for the active stake is calculated based on the currently delegated stake
        Note that this value is an estimate that the agents can easily calculate and use with the knowledge they have,
        it's not necessarily equal to the sum of all active agents' stake
        """
        # Revise active stake
        active_stake = reporters.get_active_stake_pools(self)
        self.perceived_active_stake = active_stake
        # Revise expected number of pools, k  (note that the value of beta, which is used to calculate rewards, does not change in this case)
        self.k = math.ceil(round(active_stake / self.beta, 12))  # first rounding to 12 decimal digits to avoid floating point errors

    def adjust_params(self):
        self.current_era += 1
        change_occured = False
        for key, value in self.adjustable_params.items():
            if len(value) > self.current_era:
                setattr(self, key, value[self.current_era])
                change_occured = True
                if key == 'k':
                    self.k = int(self.k)
                    # update beta in case the value of k changes
                    self.beta = self.total_stake / self.k
        if change_occured:
            self.pivot_steps.append(self.schedule.steps)

    def wrap_up_execution(self):
        self.running = False
        print("Execution {} took  {:.2f} seconds to run.".format(self.execution_id, time.time() - self.start_time))
        self.export_pools_file()
        self.export_agents_file()
        self.export_metrics_file()
        self.save_model_state_pkl()
        if self.generate_graphs:
            self.export_graphs()

