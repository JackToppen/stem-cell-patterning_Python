import numpy as np
from numba import cuda
import math
import random as r

import backend


@backend.record_time
def cell_death(simulation):
    """ Marks the cell for removal if it meets
        the criteria for cell death
    """
    for index in range(simulation.number_cells):
        # checks to see if cell is pluripotent
        if simulation.states[index] == "Pluripotent":

            # gets the number of neighbors for a cell, increasing the death counter if not enough neighbors
            if len(simulation.neighbor_graph.neighbors(index)) < simulation.lonely_cell:
                simulation.death_counters[index] += 1

            # if not, reset the death counter back to zero
            else:
                simulation.death_counters[index] = 0

            # add cell to removal array if it meets the parameters
            if simulation.death_counters[index] >= simulation.death_thresh:
                simulation.cells_to_remove = np.append(simulation.cells_to_remove, index)


@backend.record_time
def cell_diff_surround(simulation):
    """ Simulates differentiated cells inducing the
        differentiation of a pluripotent cell
    """
    for index in range(simulation.number_cells):
        # checks to see if cell is pluripotent and GATA6 low/medium
        if simulation.states[index] == "Pluripotent" and simulation.GATA6[index] < simulation.field - 1:

            # get the list of neighbors for the cell
            neighbors = simulation.neighbor_graph.neighbors(index)

            # loop over neighbors, counting the ones that are differentiated
            num_diff_neighbors = 0
            for neighbor_index in neighbors:
                # checks to see if current neighbor is differentiated and if so add to the counter
                if simulation.states[neighbor_index] == "Differentiated":
                    num_diff_neighbors += 1

                # if the number of differentiated meets the threshold, set the cell as gata6 high and nanog low
                if num_diff_neighbors >= 6:
                    simulation.GATA6[index] = simulation.field - 1
                    simulation.NANOG[index] = 0
                    break


@backend.record_time
def cell_growth(simulation):
    """ Simulates the growth of a cell currently linear,
        radius-based growth
    """
    for index in range(simulation.number_cells):
        # increase the cell radius based on the state and whether or not it has reached the max size
        if simulation.radii[index] < simulation.max_radius:
            division_count = simulation.div_counters[index]

            # pluripotent growth
            if simulation.states[index] == "Pluripotent":
                simulation.radii[index] = simulation.pluri_growth * division_count + simulation.min_radius

            # differentiated growth
            else:
                simulation.radii[index] = simulation.diff_growth * division_count + simulation.min_radius


@backend.record_time
def cell_division(simulation):
    """ Increases the counter to division and if the
        cell meets criteria marks it for division
    """
    for index in range(simulation.number_cells):
        # pluripotent cell
        if simulation.states[index] == "Pluripotent":

            # check the division counter against the threshold, add to array if dividing
            if simulation.div_counters[index] >= simulation.pluri_div_thresh:
                simulation.cells_to_divide = np.append(simulation.cells_to_divide, index)

            # if under, stochastically increase the division counter by either 0 or 1
            else:
                simulation.div_counters[index] += r.randint(0, 1)

        # differentiated cell
        else:
            # check the division counter against the threshold, add to array if dividing
            if simulation.div_counters[index] >= simulation.diff_div_thresh:

                # check for contact inhibition
                if len(simulation.neighbor_graph.neighbors(index)) < 6:
                    simulation.cells_to_divide = np.append(simulation.cells_to_divide, index)

            # if under, stochastically increase the division counter by either 0 or 1
            else:
                simulation.div_counters[index] += r.randint(0, 1)


@backend.record_time
def cell_pathway(simulation):
    """ Updates finite dynamical system variables and
        extracellular conditions
    """
    for index in range(simulation.number_cells):
        # add FGF4 to the gradient based on the cell's value of NANOG
        if simulation.NANOG[index] > 0:

            # get the amount to add, positive if adding, negative if removing
            amount = simulation.NANOG[index]
            backend.update_concentrations(simulation, "fgf4_values", index, amount, "nearest")
            # backend.update_concentrations(simulation, "fgf4_alt", index, amount, "distance")

        # activate the following pathway based on if dox (after 24 hours) has been induced yet
        if simulation.current_step > 48:

            # get a FGF4 value for the FDS based on the concentration of FGF4
            fgf4_value = backend.get_concentration(simulation, "fgf4_values", index)

            # if FDS is boolean
            if simulation.field == 2:
                # base thresholds on the maximum concentrations
                if fgf4_value > simulation.max_concentration * 0.5:
                    fgf4_fds = 1    # FGF4 high
                else:
                    fgf4_fds = 0    # FGF4 low

            # otherwise assume ternary for now
            else:
                # base thresholds on the maximum concentrations
                if fgf4_value > simulation.max_concentration * 2/3:
                    fgf4_fds = 2    # FGF4 high
                elif fgf4_value > simulation.max_concentration * 1/3:
                    fgf4_fds = 1    # FGF4 medium
                else:
                    fgf4_fds = 0    # FGF4 low

            # temporarily hold the FGFR value
            temp_fgfr = simulation.FGFR[index]

            # if updating the FDS values this step
            if simulation.fds_counters[index] % simulation.fds_thresh == 0:
                # get the current FDS values of the cell
                x1 = fgf4_fds
                x2 = simulation.FGFR[index][0]
                x3 = simulation.ERK[index][1]
                x4 = simulation.GATA6[index][2]
                x5 = simulation.NANOG[index][3]

                # if the FDS is boolean
                if simulation.field == 2:
                    # update boolean values based on FDS functions
                    new_fgfr = (x1 * x4) % 2
                    new_erk = x2 % 2
                    new_gata6 = (1 + x5 + x5 * x4) % 2
                    new_nanog = ((x3 + 1) * (x4 + 1)) % 2

                # otherwise assume ternary
                else:
                    # update ternary values based on FDS functions
                    new_fgfr = (x1*x4*((2*x1 + 1)*(2*x4 + 1) + x1*x4)) % 3
                    new_erk = x2 % 3
                    new_gata6 = ((x4**2)*(x5 + 1) + (x5**2)*(x4 + 1) + 2*x5 + 1) % 3
                    new_nanog = (x5**2 + x5*(x5+1)*(x3*(2*x4**2 + 2*x3 + 1) + x4*(2*x3**2 + 2*x4 + 1)) +
                                 (2*x3**2 + 1)*(2*x4**2 + 1)) % 3

                # if the amount of FGFR has increased, subtract that much FGF4 from the gradient
                fgfr_change = new_fgfr - temp_fgfr
                if fgfr_change > 0:
                    backend.update_concentrations(simulation, "fgf4_values", index, -1 * fgfr_change, "nearest")

                # update the FDS values of the cell
                simulation.FGFR[index] = new_fgfr
                simulation.ERK[index] = new_erk
                simulation.GATA6[index] = new_gata6
                simulation.NANOG[index] = new_nanog

            # increase the finite dynamical system counter
            simulation.fds_counters[index] += 1

            # if the cell is GATA6 high and pluripotent
            if simulation.GATA6[index] == simulation.field - 1 and simulation.states[index] == "Pluripotent":

                # increase the differentiation counter by 0 or 1
                simulation.diff_counters[index] += r.randint(0, 1)

                # if the differentiation counter is greater than or equal to the threshold, differentiate
                if simulation.diff_counters[index] >= simulation.pluri_to_diff:
                    # change the state to differentiated
                    simulation.states[index] = "Differentiated"

                    # make sure NANOG is low
                    simulation.NANOG[index] = 0

                    # allow the cell to actively move again
                    simulation.motion[index] = True


@backend.record_time
def cell_motility(simulation):
    """ gives the cells a motive force depending on
        set rules for the cell types.
    """
    # this is the motility force of the cells
    motility_force = 0.000000002

    # loop over all of the cells
    for index in range(simulation.number_cells):
        # get the neighbors of the cell
        neighbors = simulation.neighbor_graph.neighbors(index)

        # if the cell state is differentiated
        if simulation.states[index] == "Differentiated":
            # if not surrounded 6 or more cells, move away from surrounding nanog high cells
            if len(neighbors) < 6:
                # set motion to True
                simulation.motion[index] = True

                # create a vector to hold the sum of normal vectors between a cell and its neighbors
                vector_holder = np.array([0.0, 0.0, 0.0])

                # loop over the neighbors
                count = 0
                for j in range(len(neighbors)):
                    # if neighbor is nanog high, add vector to the cell to the holder
                    if simulation.NANOG[neighbors[j]] > simulation.GATA6[neighbors[j]]:
                        count += 1
                        vector = simulation.locations[neighbors[j]] - simulation.locations[index]
                        vector_holder += vector

                # if there is at least one nanog high cell move away from it
                if count > 0:
                    # get the normal vector
                    normal = backend.normal_vector(vector_holder)

                    # move in direction opposite to nanog high cells
                    simulation.motility_forces[index] += motility_force * normal * -1

                # if no nanog high cells around, move randomly
                else:
                    simulation.motility_forces[index] += backend.random_vector(simulation) * motility_force

            # set the motion to False
            else:
                simulation.motion[index] = False

        # if the cell is gata6 high and nanog low
        elif simulation.GATA6[index] > simulation.NANOG[index]:
            # if not surrounded 6 or more cells
            if len(neighbors) < 6:
                # set motion to True
                simulation.motion[index] = True

                # continue if using Guye et al. movement and if there exists differentiated cells
                if simulation.guye_move and simulation.nearest_diff[index] != -1:
                    # get the differentiated neighbor
                    guye_neighbor = simulation.cell_nearest_diff[index]

                    # get the normal vector
                    vector = simulation.locations[guye_neighbor] - simulation.locations[index]
                    normal = backend.normal_vector(vector)

                    # calculate the motility force
                    simulation.motility_forces[index] += normal * motility_force

                # if no Guye movement or no differentiated cells nearby, move randomly
                else:
                    simulation.motility_forces[index] += backend.random_vector(simulation) * motility_force

            # set the motion to False
            else:
                simulation.motion[index] = False

        # if the cell is nanog high and gata6 low
        elif simulation.NANOG[index] > simulation.GATA6[index]:
            # if not surrounded 6 or more cells
            if len(neighbors) < 6:
                # set motion to True
                simulation.motion[index] = True

                # move based on Eunbi's rules
                if simulation.eunbi_move:
                    # if there is a gata6 high cell nearby, move away from it
                    if simulation.nearest_gata6[index] != -1:
                        nearest_index = simulation.nearest_gata6[index]
                        vector = simulation.locations[nearest_index] - simulation.locations[index]
                        normal = backend.normal_vector(vector)
                        simulation.motility_forces[index] += normal * motility_force

                    # if there is a nanog high cell nearby, move to it
                    elif simulation.nearest_nanog[index] != -1:
                        nearest_index = simulation.nearest_nanog[index]
                        vector = simulation.locations[nearest_index] - simulation.locations[index]
                        normal = backend.normal_vector(vector)
                        simulation.motility_forces[index] += normal * motility_force

                    # if nothing else, move randomly
                    else:
                        simulation.motility_forces[index] += backend.random_vector(simulation) * motility_force

                # if no specific movement type, move randomly
                else:
                    simulation.motility_forces[index] += backend.random_vector(simulation) * motility_force

            # set the motion to False
            else:
                simulation.motion[index] = False

        # if both gata6/nanog high or both low
        else:
            # if not surrounded 6 or more cells
            if len(neighbors) < 6:
                simulation.motility_forces[index] += backend.random_vector(simulation) * motility_force

            # set the motion to False
            else:
                simulation.motion[index] = False


@backend.record_time
def alt_cell_motility(simulation):
    """ gives the cells a motive force depending on
        set rules for the cell types expect these rules
        are very similar to NetLogo
    """
    # this is the motility force of the cells
    motility_force = 0.000000002

    # loop over all of the cells
    for i in range(simulation.number_cells):
        # see if the cell is moving or not
        if simulation.cell_motion[i]:
            # get the neighbors of the cell if the cell is actively moving
            neighbors = simulation.neighbor_graph.neighbors(i)

            # if cell is surrounded by other cells, inhibit the motion
            if len(neighbors) >= 6:
                simulation.cell_motion[i] = False

            # if not, calculate the active movement for the step
            else:
                if simulation.cell_states[i] == "Differentiated":
                    # if there is a nanog high cell nearby, move away from it
                    if simulation.cell_nearest_nanog[i] != -1:
                        nearest_index = simulation.cell_nearest_nanog[i]
                        vector = simulation.cell_locations[nearest_index] - simulation.cell_locations[i]
                        normal = backend.normal_vector(vector)
                        simulation.cell_motility_force[i] += normal * motility_force * -1

                    # if no nearby nanog high cells, move randomly
                    else:
                        simulation.cell_motility_force[i] += backend.random_vector(simulation) * motility_force

                # if the cell is gata6 high and nanog low
                elif simulation.cell_fds[i][2] > simulation.cell_fds[i][3]:
                    # if there is a differentiated cell nearby, move toward it
                    if simulation.cell_nearest_diff[i] != -1:
                        nearest_index = simulation.cell_nearest_diff[i]
                        vector = simulation.cell_locations[nearest_index] - simulation.cell_locations[i]
                        normal = backend.normal_vector(vector)
                        simulation.cell_motility_force[i] += normal * motility_force

                    # if no nearby differentiated cells, move randomly
                    else:
                        simulation.cell_motility_force[i] += backend.random_vector(simulation) * motility_force

                # if the cell is nanog high and gata6 low
                elif simulation.cell_fds[i][3] > simulation.cell_fds[i][2]:
                    # if there is a nanog high cell nearby, move toward it
                    if simulation.cell_nearest_nanog[i] != -1:
                        nearest_index = simulation.cell_nearest_nanog[i]
                        vector = simulation.cell_locations[nearest_index] - simulation.cell_locations[i]
                        normal = backend.normal_vector(vector)
                        simulation.cell_motility_force[i] += normal * motility_force * 0.8
                        simulation.cell_motility_force[i] += backend.random_vector(simulation) * motility_force * 0.2

                    # if there is a gata6 high cell nearby, move away from it
                    elif simulation.cell_nearest_gata6[i] != -1:
                        nearest_index = simulation.cell_nearest_gata6[i]
                        vector = simulation.cell_locations[nearest_index] - simulation.cell_locations[i]
                        normal = backend.normal_vector(vector)
                        simulation.cell_motility_force[i] += normal * motility_force * -1

                    else:
                        simulation.cell_motility_force[i] += backend.random_vector(simulation) * motility_force

                # if both gata6/nanog high or both low, move randomly
                else:
                    simulation.cell_motility_force[i] += backend.random_vector(simulation) * motility_force


@backend.record_time
def check_neighbors(simulation):
    """ for all cells, determines which cells fall within a fixed
        radius to denote a neighbor then stores this information
        in a graph (uses a bin/bucket sorting method)
    """
    # radius of search (meters) in which all cells within are classified as neighbors
    neighbor_distance = 0.00001

    # if a static variable has not been created to hold the maximum number of neighbors, create one
    if not hasattr(check_neighbors, "max_neighbors"):
        # begin with a low number of neighbors that can be revalued if the max number of neighbors exceeds this value
        check_neighbors.max_neighbors = 5

    # if a static variable has not been created to hold the maximum number of cells in a bin, create one
    if not hasattr(check_neighbors, "max_cells"):
        # begin with a low number of cells that can be revalued if the max number of cells exceeds this value
        check_neighbors.max_cells = 5

    # clear all of the edges in the neighbor graph
    simulation.neighbor_graph.delete_edges(None)

    # calls the function that generates an array of bins that generalize the cell locations in addition to a
    # creating a helper array that assists the search method in counting cells in a particular bin
    bins, bins_help, bin_locations, max_cells = backend.assign_bins(simulation, neighbor_distance,
                                                                    check_neighbors.max_cells)

    # update the value of the max number of cells in a bin and double it
    check_neighbors.max_cells = max_cells

    # this will run once if all edges are included in edge_holder breaking the loop. if not, this will
    # run a second time with an updated value for the number of predicted neighbors such that all edges are included
    while True:
        # create an array used to hold edges, an array to say if edge exists, and an array to count the edges per cell
        length = simulation.number_cells * check_neighbors.max_neighbors
        edge_holder = np.zeros((length, 2), dtype=int)
        if_edge = np.zeros(length, dtype=bool)
        edge_count = np.zeros(simulation.number_cells, dtype=int)

        # call the nvidia gpu version
        if simulation.parallel:
            # turn the following into arrays that can be interpreted by the gpu
            bin_locations_cuda = cuda.to_device(bin_locations)
            cell_locations_cuda = cuda.to_device(simulation.locations)
            bins_cuda = cuda.to_device(bins)
            bins_help_cuda = cuda.to_device(bins_help)
            distance_cuda = cuda.to_device(neighbor_distance)
            edge_holder_cuda = cuda.to_device(edge_holder)
            if_edge_cuda = cuda.to_device(if_edge)
            edge_count_cuda = cuda.to_device(edge_count)
            max_neighbors_cuda = cuda.to_device(check_neighbors.max_neighbors)

            # allocate threads and blocks for gpu memory "threads per block" and "blocks per grid"
            tpb = 72
            bpg = math.ceil(simulation.number_cells / tpb)

            # call the cuda kernel with given parameters
            backend.check_neighbors_gpu[bpg, tpb](bin_locations_cuda, cell_locations_cuda, bins_cuda, bins_help_cuda,
                                                  distance_cuda, edge_holder_cuda, if_edge_cuda, edge_count_cuda,
                                                  max_neighbors_cuda)

            # return the arrays back from the gpu
            edge_holder = edge_holder_cuda.copy_to_host()
            if_edge = if_edge_cuda.copy_to_host()
            edge_count = edge_count_cuda.copy_to_host()

        # call the jit cpu version
        else:
            edge_holder, if_edge, edge_count = backend.check_neighbors_cpu(simulation.number_cells, bin_locations,
                                                                           simulation.locations, bins, bins_help,
                                                                           neighbor_distance, edge_holder, if_edge,
                                                                           edge_count, check_neighbors.max_neighbors)

        # either break the loop if all neighbors were accounted for or revalue the maximum number of neighbors
        # based on the output of the function call and double it for future calls
        max_neighbors = np.amax(edge_count)
        if check_neighbors.max_neighbors >= max_neighbors:
            break
        else:
            check_neighbors.max_neighbors = max_neighbors * 2

    # reduce the edges to only edges that actually exist
    edge_holder = edge_holder[if_edge]

    # add the edges to the neighbor graph
    simulation.neighbor_graph.add_edges(edge_holder)


@backend.record_time
def update_diffusion(simulation):
    """ goes through all extracellular gradients and
        approximates the diffusion of that molecule
    """
    # get the number of times diffusion is calculated
    diffuse_steps = int(simulation.step_dt / simulation.diffuse_dt)

    # go through all gradients and update the diffusion of each
    for gradient_name in simulation.gradient_names:
        # get the gradient array
        gradient = simulation.__dict__[gradient_name]

        # set max and min concentration values
        gradient[gradient > simulation.max_concentration] = simulation.max_concentration
        gradient[gradient < 0] = 0

        # add edges for initial conditions
        base = np.pad(gradient, 1, mode="constant", constant_values=0)

        # call the backend function to do so
        gradient = backend.update_diffusion_cpu(base, diffuse_steps, simulation.diffuse_dt, simulation.spat_res2,
                                                simulation.diffuse, simulation.size)

        # set max and min concentration values
        gradient[gradient > simulation.max_concentration] = simulation.max_concentration
        gradient[gradient < 0] = 0

        # update the gradient
        simulation.__dict__[gradient_name] = gradient


@backend.record_time
def update_queue(simulation):
    """ add and removes cells to and from the simulation
        either all at once or in "groups"
    """
    # give how many cells are being added/removed during a given step
    print("Adding " + str(len(simulation.cells_to_divide)) + " cells...")
    print("Removing " + str(len(simulation.cells_to_remove)) + " cells...")

    # Division
    # get the indices of the dividing cells
    indices = simulation.cells_to_divide

    # go through all instance variable names and copy the values of the dividing cells to end of the array
    for name in simulation.cell_array_names:
        # get the instance variable from the class attribute dictionary
        values = simulation.__dict__[name][indices]

        # if the instance variable is 1-dimensional
        if simulation.__dict__[name].ndim == 1:
            simulation.__dict__[name] = np.concatenate((simulation.__dict__[name], values))
        # if the instance variable is 2-dimensional
        else:
            simulation.__dict__[name] = np.concatenate((simulation.__dict__[name], values), axis=0)

    # go through the dividing cells and update the mother and daughter cells
    for i in range(len(simulation.cells_to_divide)):
        mother_index = simulation.cells_to_divide[i]
        daughter_index = simulation.number_cells + i

        # move the cells to positions that are representative of the new locations of daughter cells
        division_position = backend.random_vector(simulation) * (simulation.max_radius - simulation.min_radius)
        simulation.locations[mother_index] += division_position
        simulation.locations[daughter_index] -= division_position

        # reduce both radii to minimum size and set the division counters to zero
        simulation.radii[mother_index] = simulation.radii[daughter_index] = simulation.min_radius
        simulation.div_counters[mother_index] = simulation.div_counters[daughter_index] = 0

    # get the number of cells to be added
    remaining = len(simulation.cells_to_divide)

    # if not adding all cells at once
    if simulation.group != 0:
        # Cannot add all of the new cells, otherwise several cells are likely to be added in
        #   close proximity to each other at later time steps. Such addition, coupled with
        #   handling collisions, make give rise to sudden changes in overall positions of
        #   cells within the simulation. Instead, collisions are handled after 'group' number
        #   of cells are added.

        # stagger the addition of cells, subtracting from the remaining number to add
        while remaining > 0:
            # if more cells than how many we would add in a group
            if remaining >= simulation.group:
                # add the group number of cells
                n = simulation.group
            else:
                # if less than the group, only add the remaining number
                n = remaining

            # add the number of new cells to the following graphs
            simulation.neighbor_graph.add_vertices(n)
            simulation.jkr_graph.add_vertices(n)

            # increase the number of cells by how many were added
            simulation.number_cells += n

            # call the handle movement function given the addition of specified number of cells
            handle_movement(simulation)

            # subtract how many were added
            remaining -= n

    # add the cells in all at once
    else:
        simulation.neighbor_graph.add_vertices(remaining)
        simulation.jkr_graph.add_vertices(remaining)
        simulation.number_cells += remaining

    # Removal
    # get the indices of the cells leaving the simulation
    indices = simulation.cells_to_remove

    # go through the cell arrays remove the indices
    for name in simulation.cell_array_names:
        # if the array is 1-dimensional
        if simulation.__dict__[name].ndim == 1:
            simulation.__dict__[name] = np.delete(simulation.__dict__[name], indices)
        # if the array is 2-dimensional
        else:
            simulation.__dict__[name] = np.delete(simulation.__dict__[name], indices, axis=0)

    # update the graphs and number of cells
    simulation.neighbor_graph.delete_vertices(indices)
    simulation.jkr_graph.delete_vertices(indices)
    simulation.number_cells -= len(simulation.cells_to_remove)

    # clear the arrays for the next step
    simulation.cells_to_divide = np.array([], dtype=int)
    simulation.cells_to_remove = np.array([], dtype=int)


@backend.record_time
def nearest(simulation):
    """ looks at cells within a given radius a determines
        the closest cells of certain types
    """
    # radius of search for nearest cells
    nearest_distance = 0.000015

    # if a static variable has not been created to hold the maximum number of cells in a bin, create one
    if not hasattr(nearest, "max_cells"):
        # begin with a low number of cells that can be revalued if the max number of cells exceeds this value
        nearest.max_cells = 5

    # calls the function that generates an array of bins that generalize the cell locations in addition to a
    # creating a helper array that assists the search method in counting cells in a particular bin
    bins, bins_help, bin_locations, max_cells = backend.assign_bins(simulation, nearest_distance, nearest.max_cells)

    # update the value of the max number of cells in a bin and double it
    nearest.max_cells = max_cells

    # turn the following arrays into True/False
    if_diff = simulation.states == "Differentiated"

    # call the nvidia gpu version
    if simulation.parallel:
        # turn the following into arrays that can be interpreted by the gpu
        locations_cuda = cuda.to_device(simulation.locations)
        bins_cuda = cuda.to_device(bins)
        bins_help_cuda = cuda.to_device(bins_help)
        distance_cuda = cuda.to_device(nearest_distance)
        if_diff_cuda = cuda.to_device(if_diff)
        gata6_cuda = cuda.to_device(simulation.GATA6)
        nanog_cuda = cuda.to_device(simulation.NANOG)
        nearest_gata6_cuda = cuda.to_device(simulation.nearest_gata6)
        nearest_nanog_cuda = cuda.to_device(simulation.nearest_nanog)
        nearest_diff_cuda = cuda.to_device(simulation.nearest_diff)

        # allocate threads and blocks for gpu memory "threads per block" and "blocks per grid"
        tpb = 72
        bpg = math.ceil(simulation.number_cells / tpb)

        # call the cuda kernel with given parameters
        backend.nearest_gpu[bpg, tpb](locations_cuda, bins_cuda, bins_help_cuda, distance_cuda, if_diff_cuda,
                                      gata6_cuda, nanog_cuda, nearest_gata6_cuda, nearest_nanog_cuda, nearest_diff_cuda)

        # return the new nearest arrays back from the gpu
        gata6 = nearest_gata6_cuda.copy_to_host()
        nanog = nearest_nanog_cuda.copy_to_host()
        diff = nearest_diff_cuda.copy_to_host()

    # call the cpu version
    else:
        gata6, nanog, diff = backend.nearest_cpu(simulation.number_cells, simulation.cell_locations, bins, bins_help,
                                                 nearest_distance, if_diff, simulation.GATA6, simulation.NANOG,
                                                 simulation.nearest_gata6, simulation.nearest_nanog,
                                                 simulation.nearest_diff)

    # revalue the array holding the indices of nearest cells of given type
    simulation.nearest_gata6 = gata6
    simulation.nearest_nanog = nanog
    simulation.nearest_diff = diff


@backend.record_time
def handle_movement(simulation):
    """ runs the following functions together for the time period
        of the step. resets the motility force array to zero after
        movement is done
    """
    # if a static variable for holding step time hasn't been created, create one
    if not hasattr(handle_movement, "steps"):
        # get the total amount of times the cells will be incrementally moved during the step
        handle_movement.steps = math.ceil(simulation.step_dt / simulation.move_dt)

    # run the following movement functions consecutively
    for i in range(handle_movement.steps):
        # determines which cells will have physical interactions and save this to a graph
        jkr_neighbors(simulation)

        # go through the edges found in the above function and calculate resulting JKR forces
        get_forces(simulation)

        # apply all forces such as motility and JKR to the cells
        apply_forces(simulation)

    # reset motility forces back to zero vectors
    simulation.motility_forces = np.zeros((simulation.number_cells, 3), dtype=float)


@backend.record_time
def jkr_neighbors(simulation):
    """ for all cells, determines which cells will have physical
        interactions with other cells returns this information
        as an array of edges
    """
    # radius of search (meters) in which neighbors will have physical interactions, double the max cell radius
    jkr_distance = 2 * simulation.max_radius

    # if a static variable has not been created to hold the maximum number of neighbors, create one
    if not hasattr(jkr_neighbors, "max_neighbors"):
        # begin with a low number of neighbors that can be revalued if the max number of neighbors exceeds this value
        jkr_neighbors.max_neighbors = 5

    # if a static variable has not been created to hold the maximum number of cells in a bin, create one
    if not hasattr(jkr_neighbors, "max_cells"):
        # begin with a low number of cells that can be revalued if the max number of cells exceeds this value
        jkr_neighbors.max_cells = 5

    # calls the function that generates an array of bins that generalize the cell locations in addition to a
    # creating a helper array that assists the search method in counting cells in a particular bin
    bins, bins_help, bin_locations, max_cells = backend.assign_bins(simulation, jkr_distance, jkr_neighbors.max_cells)

    # update the value of the max number of cells in a bin
    jkr_neighbors.max_cells = max_cells

    # this will run once and if all edges are included in edge_holder, the loop will break. if not this will
    # run a second time with an updated value for number of predicted neighbors such that all edges are included
    while True:
        # create an array used to hold edges, an array to say where edges are, and an array to count the edges per cell
        length = simulation.number_cells * jkr_neighbors.max_neighbors
        edge_holder = np.zeros((length, 2), dtype=int)
        if_edge = np.zeros(length, dtype=bool)
        edge_count = np.zeros(simulation.number_cells, dtype=int)

        # call the nvidia gpu version
        if simulation.parallel:
            # turn the following into arrays that can be interpreted by the gpu
            bin_locations_cuda = cuda.to_device(bin_locations)
            locations_cuda = cuda.to_device(simulation.locations)
            radii_cuda = cuda.to_device(simulation.radii)
            bins_cuda = cuda.to_device(bins)
            bins_help_cuda = cuda.to_device(bins_help)
            edge_holder_cuda = cuda.to_device(edge_holder)
            if_edge_cuda = cuda.to_device(if_edge)
            edge_count_cuda = cuda.to_device(edge_count)
            max_neighbors_cuda = cuda.to_device(jkr_neighbors.max_neighbors)

            # allocate threads and blocks for gpu memory "threads per block" and "blocks per grid"
            tpb = 72
            bpg = math.ceil(simulation.number_cells / tpb)

            # call the cuda kernel with given parameters
            backend.jkr_neighbors_gpu[bpg, tpb](bin_locations_cuda, locations_cuda, radii_cuda, bins_cuda,
                                                bins_help_cuda, edge_holder_cuda, if_edge_cuda, edge_count_cuda,
                                                max_neighbors_cuda)

            # return the arrays back from the gpu
            edge_holder = edge_holder_cuda.copy_to_host()
            if_nonzero = if_edge_cuda.copy_to_host()
            edge_count = edge_count_cuda.copy_to_host()

        # call the jit cpu version
        else:
            edge_holder, if_edge, edge_count = backend.jkr_neighbors_cpu(simulation.number_cells, bin_locations,
                                                                         simulation.locations,
                                                                         simulation.radii, bins, bins_help,
                                                                         edge_holder, if_nonzero,
                                                                         edge_count, jkr_neighbors.max_neighbors)

        # either break the loop if all neighbors were accounted for or revalue the maximum number of neighbors
        # based on the output of the function call and double it
        max_neighbors = np.amax(edge_count)
        if jkr_neighbors.max_neighbors >= max_neighbors:
            break
        else:
            jkr_neighbors.max_neighbors = max_neighbors * 2

    # reduce the edges to only nonzero edges
    edge_holder = edge_holder[if_edge]

    # add the edges and simplify the graph as this is a running graph that is never cleared due to its use
    # for holding adhesive JKR bonds from step to step
    simulation.jkr_graph.add_edges(edge_holder)
    simulation.jkr_graph.simplify()


@backend.record_time
def get_forces(simulation):
    """ goes through all of "JKR" edges and quantifies any
        resulting adhesive or repulsion forces between
        pairs of cells
    """
    # parameters that rarely change
    adhesion_const = 0.000107    # the adhesion constant in kg/s from P Pathmanathan et al.
    poisson = 0.5    # Poisson's ratio for the cells, 0.5 means incompressible
    youngs = 1000    # Young's modulus for the cells in Pa

    # get the edges as a numpy array, count them, and create an array used to delete edges
    jkr_edges = np.array(simulation.jkr_graph.get_edgelist())
    number_edges = len(jkr_edges)
    delete_edges = np.zeros(number_edges, dtype=bool)

    # only continue if edges exist, if no edges compiled functions will raise errors
    if number_edges > 0:
        # call the nvidia gpu version
        if simulation.parallel:
            # turn the following into arrays that can be interpreted by the gpu
            jkr_edges_cuda = cuda.to_device(jkr_edges)
            delete_edges_cuda = cuda.to_device(delete_edges)
            locations_cuda = cuda.to_device(simulation.locations)
            radii_cuda = cuda.to_device(simulation.radii)
            forces_cuda = cuda.to_device(simulation.jkr_forces)
            poisson_cuda = cuda.to_device(poisson)
            youngs_cuda = cuda.to_device(youngs)
            adhesion_const_cuda = cuda.to_device(adhesion_const)

            # allocate threads and blocks for gpu memory "threads per block" and "blocks per grid"
            tpb = 72
            bpg = math.ceil(simulation.number_cells / tpb)

            # call the cuda kernel with given parameters
            backend.get_forces_gpu[bpg, tpb](jkr_edges_cuda, delete_edges_cuda, locations_cuda, radii_cuda, forces_cuda,
                                             poisson_cuda, youngs_cuda, adhesion_const_cuda)

            # return the new forces and the edges to be deleted
            forces = forces_cuda.copy_to_host()
            delete_edges = delete_edges_cuda.copy_to_host()

        # call the cpu version
        else:
            forces, delete_edges = backend.get_forces_cpu(jkr_edges, delete_edges, simulation.locations,
                                                          simulation.radii, simulation.jkr_forces, poisson,
                                                          youngs, adhesion_const)

        # update the jkr edges to remove any edges that have be broken and update the cell jkr forces
        delete_edges_indices = np.arange(number_edges)[delete_edges]
        simulation.jkr_graph.delete_edges(delete_edges_indices)
        simulation.jkr_forces = forces


@backend.record_time
def apply_forces(simulation):
    """ Turns the active motility/division forces and
        inactive JKR forces into movement
    """
    # parameters that rarely change
    viscosity = 10000    # the viscosity of the medium in Ns/m used for stokes friction

    # call the nvidia gpu version
    if simulation.parallel:
        # turn the following into arrays that can be interpreted by the gpu
        jkr_forces_cuda = cuda.to_device(simulation.jkr_forces)
        motility_forces_cuda = cuda.to_device(simulation.motility_forces)
        locations_cuda = cuda.to_device(simulation.locations)
        radii_cuda = cuda.to_device(simulation.radii)
        viscosity_cuda = cuda.to_device(viscosity)
        size_cuda = cuda.to_device(simulation.size)
        move_dt_cuda = cuda.to_device(simulation.move_dt)

        # allocate threads and blocks for gpu memory "threads per block" and "blocks per grid"
        tpb = 72
        bpg = math.ceil(simulation.number_cells / tpb)

        # call the cuda kernel with given parameters
        backend.apply_forces_gpu[bpg, tpb](jkr_forces_cuda, motility_forces_cuda, locations_cuda, radii_cuda,
                                           viscosity_cuda, size_cuda, move_dt_cuda)

        # return the new cell locations from the gpu
        new_locations = locations_cuda.copy_to_host()

    # call the cpu version
    else:
        new_locations = backend.apply_forces_cpu(simulation.number_cells, simulation.jkr_forces,
                                                 simulation.motility_forces, simulation.locations,
                                                 simulation.radii, viscosity, simulation.size,
                                                 simulation.move_dt)

    # update the locations and reset the jkr forces back to zero
    simulation.locations = new_locations
    simulation.jkr_forces = np.zeros((simulation.number_cells, 3), dtype=float)
