import numpy as np
import random as r
import math
import time
from numba import jit, cuda, prange
from functools import wraps


class Base:
    """ This object is the base class for the Simulation object. It's used to
        make the Simulation class look a lot less intimidating.
    """
    def __init__(self, paths, name):
        self.paths = paths    # the Paths object which holds any output paths
        self.name = name    # the name of the simulation

        # the running number of cells and the step to begin at (altered by continuation mode)
        self.number_cells = 0
        self.beginning_step = 1

        # arrays to store the cells set to divide or to be removed
        self.cells_to_divide = np.array([], dtype=int)
        self.cells_to_remove = np.array([], dtype=int)

        # various other holders
        self.cell_array_names = list()  # store the variable names of each cell array
        self.cell_types = dict()  # hold the names of cell types defined in run.py
        self.method_times = dict()  # store the runtimes of selected methods, used by record_time() decorator

        # suppresses IDE error, not necessary
        self.graph_names = None

    def add_cells(self, number, cell_type=None):
        """ Add cells to the Simulation object and potentially add a cell type
            with bounds for defining alternative initial parameters.

                number (int): the number of cells being added to the Simulation object
                cell_type (str): the name of a cell type that can be used by cell_array() to only apply
                    initial parameters to these cells, instead of the entire array.
        """
        # add specified number of cells to each graph
        for graph_name in self.graph_names:
            self.__dict__[graph_name].add_vertices(number)

        # update the running number of cells and determine bounds for slice if cell_type is used
        begin = self.number_cells
        self.number_cells += number

        # if a cell type name is passed, hold the slice bounds for that particular cell type
        if cell_type is not None:
            self.cell_types[cell_type] = (begin, self.number_cells)

    def cell_array(self, array_name, cell_type=None, dtype=float, vector=None, func=None, override=None):
        """ Create a cell array in the Simulation object used to hold values
            for all cells and optionally specify initial parameters.

                array_name (str): the name of the variable made for the cell array in the Simulation object
                cell_type (str): see add_cells()
                dtype (object): the data type of the array, defaults to float
                vector (int): the length of the vector for each cell in the array
                func (object): a function called for each index of the array to specify initial parameters
                override (array): use the array passed instead of generating a new array
        """
        # if using existing array for cell array
        if override is not None:
            # make sure array have correct length, otherwise raise error
            if override.shape[0] != self.number_cells:
                raise Exception("Length of override array does not match number of cells in simulation!")

            # use the array and add to list of cell array names
            else:
                self.__dict__[array_name] = override
                self.cell_array_names.append(array_name)

        # otherwise make sure a default cell array exists for initial parameters
        else:
            # if no cell array in Simulation object, make one
            if not hasattr(self, array_name):
                # add the array name to a list for automatic addition/removal when cells divide/die
                self.cell_array_names.append(array_name)

                # get the dimensions of the array
                if vector is None:
                    size = self.number_cells  # 1-dimensional array
                else:
                    size = (self.number_cells, vector)  # 2-dimensional array (1-dimensional of vectors)

                # if using python string data type, use object data type instead
                if dtype == str or dtype == object:
                    # create cell array in Simulation object with NoneType as default value
                    self.__dict__[array_name] = np.empty(size, dtype=object)

                else:
                    # create cell array in Simulation object, with zeros as default values
                    self.__dict__[array_name] = np.zeros(size, dtype=dtype)

        # if no cell type parameter passed
        if cell_type is None:
            # if function is passed, apply initial parameter
            if func is not None:
                for i in range(self.number_cells):
                    self.__dict__[array_name][i] = func()

        # otherwise a cell type is passed
        else:
            # get the bounds of the slice
            begin = self.cell_types[cell_type][0]
            end = self.cell_types[cell_type][1]

            # if function is passed, apply initial parameter to slice
            if func is not None:
                for i in range(begin, end):
                    self.__dict__[array_name][i] = func()


def assign_bins(simulation, distance, max_cells):
    """ Generalizes cell locations to a bin within lattice imposed on
        the cell space, used for a parallel fixed-radius neighbor search.
    """
    # If there is enough space for all cells that should be in a bin, break out of the loop. If there isn't
    # update the amount of needed space and put all the cells in bins. This will run once if the prediction
    # of max neighbors suffices, twice if it isn't right the first time.
    while True:
        # calculate the size of the array used to represent the bins and the bins helper array, include extra bins
        # for cells that may fall outside of the space
        bins_help_size = np.ceil(simulation.size / distance).astype(int) + 3
        bins_size = np.append(bins_help_size, max_cells)

        # create the arrays for "bins" and "bins_help"
        bins_help = np.zeros(bins_help_size, dtype=int)    # holds the number of cells currently in a bin
        bins = np.empty(bins_size, dtype=int)    # holds the indices of cells in a bin

        # generalize the cell locations to bin indices and offset by 1 to prevent missing cells that fall out of the
        # simulation space
        bin_locations = np.floor_divide(simulation.locations, distance).astype(int)
        bin_locations += 1

        # use jit function to speed up placement of cells
        bins, bins_help = assign_bins_jit(simulation.number_cells, bin_locations, bins, bins_help)

        # either break the loop if all cells were accounted for or revalue the maximum number of cells based on
        # the output of the function call and double it future calls
        new_max_cells = np.amax(bins_help)
        if max_cells >= new_max_cells:
            break
        else:
            max_cells = new_max_cells * 2   # double to prevent continual updating

    return bins, bins_help, bin_locations, max_cells


@jit(nopython=True, cache=True)
def assign_bins_jit(number_cells, bin_locations, bins, bins_help):
    """ A just-in-time compiled function for assign_bins() that places
        the cells in their respective bins.
    """
    # go through all cells
    for index in range(number_cells):
        # get the indices of the generalized cell location
        x, y, z = bin_locations[index]

        # use the help array to get the new index for the cell in the bin
        place = bins_help[x][y][z]

        # adds the index in the cell array to the bin
        bins[x][y][z][place] = index

        # update the number of cells in a bin
        bins_help[x][y][z] += 1

    # return the arrays now filled with cell indices
    return bins, bins_help


@cuda.jit
def get_neighbors_gpu(bin_locations, locations, bins, bins_help, distance, edge_holder, if_edge, edge_count,
                      max_neighbors):
    """ A just-in-time compiled cuda kernel for the get_neighbors()
        method that performs the actual calculations.
    """
    # get the index in the array
    focus = cuda.grid(1)

    # get the starting index for writing to the edge holder array
    start = focus * max_neighbors[0]

    # double check that focus index is within the array
    if focus < bin_locations.shape[0]:
        # holds the total amount of edges for a given cell
        cell_edge_count = 0

        # get the bin location of the cell
        x, y, z = bin_locations[focus]

        # go through the surrounding bins including the bin the cell is in
        for i in range(-1, 2):
            for j in range(-1, 2):
                for k in range(-1, 2):
                    # get the count of cells for the current bin
                    bin_count = bins_help[x + i][y + j][z + k]

                    # go through the bin determining if a cell is a neighbor
                    for l in range(bin_count):
                        # get the index of the current potential neighbor
                        current = bins[x + i][y + j][z + k][l]

                        # check to see if that cell is within the search radius and only continue if the current cell
                        # has a higher index to prevent double counting edges
                        if magnitude(locations[focus], locations[current]) <= distance[0] and focus < current:
                            # if less than the max edges, add the edge
                            if cell_edge_count < max_neighbors[0]:
                                # get the index for the edge
                                index = start + cell_edge_count

                                # update the edge array and identify that this edge exists
                                edge_holder[index][0] = focus
                                edge_holder[index][1] = current
                                if_edge[index] = 1

                            # increase the count of edges for a cell and the index for the next edge
                            cell_edge_count += 1

        # update the array with number of edges for the cell
        edge_count[focus] = cell_edge_count


@jit(nopython=True, parallel=True, cache=True)
def get_neighbors_cpu(number_cells, bin_locations, locations, bins, bins_help, distance, edge_holder, if_edge,
                      edge_count, max_neighbors):
    """ A just-in-time compiled function for the get_neighbors()
        method that performs the actual calculations.
    """
    # loops over all cells, with the current cell index being the focus
    for focus in prange(number_cells):
        # get the starting index for writing to the edge holder array
        start = focus * max_neighbors

        # holds the total amount of edges for a given cell
        cell_edge_count = 0

        # get the bin location of the cell
        x, y, z = bin_locations[focus]

        # go through the surrounding bins including the bin the cell is in
        for i in range(-1, 2):
            for j in range(-1, 2):
                for k in range(-1, 2):
                    # get the count of cells for the current bin
                    bin_count = bins_help[x + i][y + j][z + k]

                    # go through the bin determining if a cell is a neighbor
                    for l in range(bin_count):
                        # get the index of the current potential neighbor
                        current = bins[x + i][y + j][z + k][l]

                        # check to see if that cell is within the search radius and only continue if the current cell
                        # has a higher index to prevent double counting edges
                        if np.linalg.norm(locations[current] - locations[focus]) <= distance and focus < current:
                            # if less than the max edges, add the edge
                            if cell_edge_count < max_neighbors:
                                # get the index for the edge
                                index = start + cell_edge_count

                                # update the edge array and identify that this edge exists
                                edge_holder[index][0] = focus
                                edge_holder[index][1] = current
                                if_edge[index] = 1

                            # increase the count of edges for a cell and the index for the next edge
                            cell_edge_count += 1

        # update the array with number of edges for the cell
        edge_count[focus] = cell_edge_count

    return edge_holder, if_edge, edge_count


@cuda.jit
def jkr_neighbors_gpu(bin_locations, locations, radii, bins, bins_help, edge_holder, if_edge, edge_count,
                      max_neighbors):
    """ A just-in-time compiled cuda kernel for the jkr_neighbors()
        method that performs the actual calculations.
    """
    # get the index in the array
    focus = cuda.grid(1)

    # get the starting index for writing to the edge holder array
    start = focus * max_neighbors[0]

    # double check that focus index is within the array
    if focus < locations.shape[0]:
        # holds the total amount of edges for a given cell
        cell_edge_count = 0

        # get the bin location of the cell
        x, y, z = bin_locations[focus]

        # go through the surrounding bins including the bin the cell is in
        for i in range(-1, 2):
            for j in range(-1, 2):
                for k in range(-1, 2):
                    # get the count of cells for the current bin
                    bin_count = bins_help[x + i][y + j][z + k]

                    # go through the bin determining if a cell is a neighbor
                    for l in range(bin_count):
                        # get the index of the current potential neighbor
                        current = bins[x + i][y + j][z + k][l]

                        # get the magnitude of the distance vector between the cell locations
                        mag = magnitude(locations[focus], locations[current])

                        # calculate the overlap of the cells
                        overlap = radii[focus] + radii[current] - mag

                        # if there is 0 or more overlap and if the current cell has a higher index to prevent double
                        # counting edges
                        if overlap >= 0 and focus < current:
                            # if less than the max edges, add the edge
                            if cell_edge_count < max_neighbors[0]:
                                # get the index for the edge
                                index = start + cell_edge_count

                                # update the edge array and identify that this edge exists
                                edge_holder[index][0] = focus
                                edge_holder[index][1] = current
                                if_edge[index] = 1

                            # increase the count of edges for a cell and the index for the next edge
                            cell_edge_count += 1

        # update the array with number of edges for the cell
        edge_count[focus] = cell_edge_count


@jit(nopython=True, parallel=True, cache=True)
def jkr_neighbors_cpu(number_cells, bin_locations, locations, radii, bins, bins_help, edge_holder,
                      if_edge, edge_count, max_neighbors):
    """ A just-in-time compiled function for the jkr_neighbors()
        method that performs the actual calculations.
    """
    # loops over all cells, with the current cell index being the focus
    for focus in prange(number_cells):
        # get the starting index for writing to the edge holder array
        start = focus * max_neighbors

        # holds the total amount of edges for a given cell
        cell_edge_count = 0

        # get the bin location of the cell
        x, y, z = bin_locations[focus]

        # go through the surrounding bins including the bin the cell is in
        for i in range(-1, 2):
            for j in range(-1, 2):
                for k in range(-1, 2):
                    # get the count of cells for the current bin
                    bin_count = bins_help[x + i][y + j][z + k]

                    # go through the bin determining if a cell is a neighbor
                    for l in range(bin_count):
                        # get the index of the current potential neighbor
                        current = bins[x + i][y + j][z + k][l]

                        # get the magnitude of the distance vector between the cell locations
                        mag = np.linalg.norm(locations[current] - locations[focus])

                        # calculate the overlap of the cells
                        overlap = radii[current] + radii[focus] - mag

                        # if there is 0 or more overlap and if the current cell has a higher index to prevent double
                        # counting edges
                        if overlap >= 0 and focus < current:
                            # if less than the max edges, add the edge
                            if cell_edge_count < max_neighbors:
                                # get the index for the edge
                                index = start + cell_edge_count

                                # update the edge array and identify that this edge exists
                                edge_holder[index][0] = focus
                                edge_holder[index][1] = current
                                if_edge[index] = 1

                            # increase the count of edges for a cell and the index for the next edge
                            cell_edge_count += 1

        # update the array with number of edges for the cell
        edge_count[focus] = cell_edge_count

    return edge_holder, if_edge, edge_count


@cuda.jit
def get_forces_gpu(jkr_edges, delete_edges, locations, radii, jkr_forces, poisson, youngs, adhesion_const):
    """ A just-in-time compiled cuda kernel for the get_forces()
        method that performs the actual calculations.
    """
    # get the index in the edges array
    edge_index = cuda.grid(1)

    # double check that index is within the array
    if edge_index < jkr_edges.shape[0]:
        # get the cell indices of the edge
        cell_1 = jkr_edges[edge_index][0]
        cell_2 = jkr_edges[edge_index][1]

        # get the locations of the two cells
        location_1 = locations[cell_1]
        location_2 = locations[cell_2]

        # get the magnitude of the distance between the cells
        mag = magnitude(location_1, location_2)

        # get the overlap of the cells
        overlap = radii[cell_1] + radii[cell_2] - mag

        # get two values used for JKR calculation
        e_hat = (((1 - poisson[0] ** 2) / youngs[0]) + ((1 - poisson[0] ** 2) / youngs[0])) ** -1
        r_hat = ((1 / radii[cell_1]) + (1 / radii[cell_2])) ** -1

        # value used to calculate the max adhesive distance after bond has been already formed
        overlap_ = (((math.pi * adhesion_const[0]) / e_hat) ** (2 / 3)) * (r_hat ** (1 / 3))

        # get the nondimensionalized overlap
        d = overlap / overlap_

        # check to see if the cells will have a force interaction based on the nondimensionalized distance
        if d > -0.360562:
            # plug the value of d into polynomial approximation for nondimensionalized force
            f = (-0.0204 * d ** 3) + (0.4942 * d ** 2) + (1.0801 * d) - 1.324

            # convert from the nondimensionalized force to find the JKR force
            jkr_force = f * math.pi * adhesion_const[0] * r_hat

            # loops over all directions of space
            for i in range(3):
                # get the vector by axis between the two cells
                vector = location_1[i] - location_2[i]

                # if the magnitude is 0 use the zero vector, otherwise find the normalized vector for each axis
                if mag != 0:
                    normal = vector / mag
                else:
                    normal = 0

                # adds the adhesive force as a vector in opposite directions to each cell's force holder
                jkr_forces[cell_1][i] += jkr_force * normal
                jkr_forces[cell_2][i] -= jkr_force * normal

        # remove the edge if the it fails to meet the criteria for distance, simulating that the bond is broken
        else:
            delete_edges[edge_index] = 1


@jit(nopython=True, parallel=True, cache=True)
def get_forces_cpu(number_edges, jkr_edges, delete_edges, locations, radii, jkr_forces, poisson, youngs,
                   adhesion_const):
    """ A just-in-time compiled function for the get_forces()
        method that performs the actual calculations.
    """
    # go through the edges array
    for edge_index in prange(number_edges):
        # get the cell indices of the edge
        cell_1 = jkr_edges[edge_index][0]
        cell_2 = jkr_edges[edge_index][1]

        # get the vector between the centers of the cells and the magnitude of this vector
        vector = locations[cell_1] - locations[cell_2]
        mag = np.linalg.norm(vector)

        # get the overlap of the cells
        overlap = radii[cell_1] + radii[cell_2] - mag

        # get two values used for JKR calculation
        e_hat = (((1 - poisson ** 2) / youngs) + ((1 - poisson ** 2) / youngs)) ** -1
        r_hat = ((1 / radii[cell_1]) + (1 / radii[cell_2])) ** -1

        # value used to calculate the max adhesive distance after bond has been already formed
        overlap_ = (((math.pi * adhesion_const) / e_hat) ** (2 / 3)) * (r_hat ** (1 / 3))

        # get the nondimensionalized overlap
        d = overlap / overlap_

        # check to see if the cells will have a force interaction based on the nondimensionalized distance
        if d > -0.360562:
            # plug the value of d into polynomial approximation for nondimensionalized force
            f = (-0.0204 * d ** 3) + (0.4942 * d ** 2) + (1.0801 * d) - 1.324

            # convert from the nondimensionalized force to find the JKR force
            jkr_force = f * math.pi * adhesion_const * r_hat

            # if the magnitude is 0 use the zero vector, otherwise find the normalized vector for each axis. numba's
            # jit prefers a reduction instead of generating a new normalized array
            normal = np.array([0.0, 0.0, 0.0])
            if mag != 0:
                normal += vector / mag

            # adds the adhesive force as a vector in opposite directions to each cell's force holder
            jkr_forces[cell_1] += jkr_force * normal
            jkr_forces[cell_2] -= jkr_force * normal

        # remove the edge if the it fails to meet the criteria for distance, simulating that the bond is broken
        else:
            delete_edges[edge_index] = 1

    return jkr_forces, delete_edges


@cuda.jit
def apply_forces_gpu(jkr_force, motility_force, locations, radii, viscosity, size, move_dt):
    """ A just-in-time compiled cuda kernel for the apply_forces()
        method that performs the actual calculations.
    """
    # get the index in the array
    index = cuda.grid(1)

    # double check that index is within the array
    if index < locations.shape[0]:
        # stokes law for velocity based on force and fluid viscosity (friction)
        stokes_friction = 6 * math.pi * viscosity[0] * radii[index]

        # loop over all directions of space
        for i in range(3):
            # update the velocity of the cell based on stokes
            velocity = (jkr_force[index][i] + motility_force[index][i]) / stokes_friction

            # set the new location
            new_location = locations[index][i] + velocity * move_dt[0]

            # check if new location is in the simulation space, if not set values at space limits
            if new_location > size[i]:
                locations[index][i] = size[i]
            elif new_location < 0:
                locations[index][i] = 0
            else:
                locations[index][i] = new_location


@jit(nopython=True, parallel=True, cache=True)
def apply_forces_cpu(number_cells, jkr_force, motility_force, locations, radii, viscosity, size, move_dt):
    """ A just-in-time compiled function for the apply_forces()
        method that performs the actual calculations.
    """
    # loop over all cells
    for i in prange(number_cells):
        # stokes law for velocity based on force and fluid viscosity (friction)
        stokes_friction = 6 * math.pi * viscosity * radii[i]

        # update the velocity of the cell based on stokes
        velocity = (motility_force[i] + jkr_force[i]) / stokes_friction

        # set the new location
        new_location = locations[i] + velocity * move_dt

        # loop over all directions of space
        for j in range(0, 3):
            # check if new location is in the space, if not return it to the space limits
            if new_location[j] > size[j]:
                locations[i][j] = size[j]
            elif new_location[j] < 0:
                locations[i][j] = 0
            else:
                locations[i][j] = new_location[j]

    return locations


@cuda.jit
def nearest_gpu(bin_locations, locations, bins, bins_help, distance, if_diff, gata6, nanog, nearest_gata6,
                nearest_nanog, nearest_diff):
    """ A just-in-time compiled cuda kernel for the nearest()
        method that performs the actual calculations.
    """
    # get the index in the array
    focus = cuda.grid(1)

    # double check that the index is within the array
    if focus < locations.shape[0]:
        # get the bin location of the cell
        x, y, z = bin_locations[focus]

        # initialize the nearest indices with -1 which will be interpreted as no cell by the motility function
        nearest_gata6_index, nearest_nanog_index, nearest_diff_index = -1, -1, -1

        # initialize the distance for each with double the search radius to provide a starting point
        nearest_gata6_dist, nearest_nanog_dist, nearest_diff_dist = distance[0] * 2, distance[0] * 2, distance[0] * 2

        # go through the surrounding bins including the bin the cell is in
        for i in range(-1, 2):
            for j in range(-1, 2):
                for k in range(-1, 2):
                    # get the count of cells for the current bin
                    bin_count = bins_help[x + i][y + j][z + k]

                    # go through the bin
                    for l in range(bin_count):
                        # get the index of the current potential nearest cell
                        current = bins[x + i][y + j][z + k][l]

                        # get the magnitude of the distance vector between the cells
                        mag = magnitude(locations[focus], locations[current])

                        # check to see if the current cell is within the search radius and not the same cell
                        if mag <= distance[0] and focus != current:
                            # if the current cell is differentiated
                            if if_diff[current]:
                                # if it's closer than the last cell, update the distance and index
                                if mag < nearest_diff_dist:
                                    nearest_diff_index = current
                                    nearest_diff_dist = mag

                            # if the current cell is gata6 high
                            elif gata6[current] > nanog[current]:
                                # if it's closer than the last cell, update the distance and index
                                if mag < nearest_gata6_dist:
                                    nearest_gata6_index = current
                                    nearest_gata6_dist = mag

                            # if the current cell is nanog high
                            elif gata6[current] < nanog[current]:
                                # if it's closer than the last cell, update the distance and index
                                if mag < nearest_nanog_dist:
                                    nearest_nanog_index = current
                                    nearest_nanog_dist = mag

        # update the arrays
        nearest_gata6[focus] = nearest_gata6_index
        nearest_nanog[focus] = nearest_nanog_index
        nearest_diff[focus] = nearest_diff_index


@jit(nopython=True, parallel=True, cache=True)
def nearest_cpu(number_cells, bin_locations, locations, bins, bins_help, distance, if_diff, gata6, nanog, nearest_gata6,
                nearest_nanog, nearest_diff):
    """ A just-in-time compiled function for the nearest()
        method that performs the actual calculations.
    """
    # loop over all cells
    for focus in prange(number_cells):
        # get the bin location of the cell
        x, y, z = bin_locations[focus]

        # initialize the nearest indices with -1 which will be interpreted as no cell by the motility function
        nearest_gata6_index, nearest_nanog_index, nearest_diff_index = -1, -1, -1

        # initialize the distance for each with double the search radius to provide a starting point
        nearest_gata6_dist, nearest_nanog_dist, nearest_diff_dist = distance * 2, distance * 2, distance * 2

        # go through the surrounding bins including the bin the cell is in
        for i in range(-1, 2):
            for j in range(-1, 2):
                for k in range(-1, 2):
                    # get the count of cells for the current bin
                    bin_count = bins_help[x + i][y + j][z + k]

                    # go through the bin
                    for l in range(bin_count):
                        # get the index of the current potential nearest cell
                        current = bins[x + i][y + j][z + k][l]

                        # get the magnitude of the distance vector between the cells
                        mag = np.linalg.norm(locations[current] - locations[focus])

                        # check to see if the current cell is within the search radius and not the same cell
                        if mag <= distance and focus != current:
                            # if the current cell is differentiated
                            if if_diff[current]:
                                # if it's closer than the last cell, update the distance and index
                                if mag < nearest_diff_dist:
                                    nearest_diff_index = current
                                    nearest_diff_dist = mag

                            # if the current cell is gata6 high
                            elif gata6[current] > nanog[current]:
                                # if it's closer than the last cell, update the distance and index
                                if mag < nearest_gata6_dist:
                                    nearest_gata6_index = current
                                    nearest_gata6_dist = mag

                            # if the current cell is nanog high
                            elif gata6[current] < nanog[current]:
                                # if it's closer than the last cell, update the distance and index
                                if mag < nearest_nanog_dist:
                                    nearest_nanog_index = current
                                    nearest_nanog_dist = mag

        # update the arrays
        nearest_gata6[focus] = nearest_gata6_index
        nearest_nanog[focus] = nearest_nanog_index
        nearest_diff[focus] = nearest_diff_index

    return nearest_gata6, nearest_nanog, nearest_diff


@jit(nopython=True, cache=True)
def update_diffusion_jit(base, steps, diffuse_dt, last_dt, diffuse_const, spat_res2):
    """ A just-in-time compiled function for update_diffusion()
        that performs the actual diffusion calculation.
    """
    # holder the following constant for faster computation, given that dx and dy match
    a = diffuse_dt * diffuse_const / spat_res2
    b = 1 - 4 * a

    # finite difference to solve laplacian diffusion equation, currently 2D
    for i in range(steps):
        # on the last step apply smaller diffuse dt if step dt doesn't divide nicely
        if i == steps - 1:
            a = last_dt * diffuse_const / spat_res2
            b = 1 - 4 * a

        # set the initial conditions by reflecting the edges of the gradient
        base[:, 0] = base[:, 1]
        base[:, -1] = base[:, -2]
        base[0, :] = base[1, :]
        base[-1, :] = base[-2, :]

        # get the morphogen addition for the diffusion points, based on other points and hold this
        temp = a * (base[2:, 1:-1] + base[:-2, 1:-1] + base[1:-1, 2:] + base[1:-1, :-2])

        # get the diffusion loss for the diffusion points
        base[1:-1, 1:-1] *= b

        # add morphogen change from the temporary array
        base[1:-1, 1:-1] += temp

    # return the gradient back without the edges
    return base[1:-1, 1:-1]


def get_concentration(simulation, gradient_name, index):
    """ Get the concentration of a gradient for a cell's
        location. Currently this uses the nearest method.
    """
    # get the gradient array from the simulation instance
    gradient = simulation.__dict__[gradient_name]

    # find the nearest diffusion point
    half_indices = np.floor(2 * simulation.locations[index] / simulation.spat_res)
    indices = np.ceil(half_indices / 2).astype(int)
    x, y, z = indices[0], indices[1], indices[2]

    # return the value of the gradient at the diffusion point
    return gradient[x][y][z]


def adjust_morphogens(simulation, gradient_name, index, amount, mode):
    """ Adjust the concentration of the gradient based on
        the amount, location of cell, and mode.
    """
    # get the gradient array from the simulation instance
    gradient = simulation.__dict__[gradient_name]

    # use the nearest method similar to the get_concentration()
    if mode == "nearest":
        # find the nearest diffusion point
        half_indices = np.floor(2 * simulation.locations[index] / simulation.spat_res)
        indices = np.ceil(half_indices / 2).astype(int)
        x, y, z = indices[0], indices[1], indices[2]

        # add the specified amount to the nearest diffusion point
        gradient[x][y][z] += amount

    # use the distance dependent method for adding concentrations, not optimized yet...
    elif mode == "distance":
        # divide the location for a cell by the spatial resolution then take the floor function of it
        indices = np.floor(simulation.locations[index] / simulation.spat_res).astype(int)
        x, y, z = indices[0], indices[1], indices[2]

        # get the four nearest points to the cell in 2D and make array for holding distances
        diffusion_points = np.array([[x, y, 0], [x+1, y, 0], [x, y+1, 0], [x+1, y+1, 0]], dtype=int)
        distances = -1 * np.ones(4, dtype=float)

        # hold the sum of the reciprocals of the distances
        total = 0

        # get the gradient size and handle each of the four nearest points
        gradient_size = simulation.gradient_size
        for i in range(4):
            # check that the diffusion point is not outside the space
            if diffusion_points[i][0] < gradient_size[0] and diffusion_points[i][1] < gradient_size[1]:
                # if ok, calculate magnitude of the distance from the cell to it
                point_location = diffusion_points[i] * simulation.spat_res
                mag = np.linalg.norm(simulation.locations[index] - point_location)
                if mag <= simulation.max_radius:
                    # save the distance and if the cell is not on top of the point add the reciprocal
                    distances[i] = mag
                    if mag != 0:
                        total += 1/mag

        # add morphogen to each diffusion point that falls within the cell radius
        for i in range(4):
            x, y, z = diffusion_points[i][0], diffusion_points[i][1], 0
            # if on top of diffusion point add all of the concentration
            if distances[i] == 0:
                gradient[x][y][z] += amount
            # if in radius add proportional amount
            elif distances[i] != -1:
                gradient[x][y][z] += amount / (distances[i] * total)
            else:
                pass

    # if some other mode
    else:
        raise Exception("Unknown mode for the adjust_morphogens() method")


def info(simulation):
    """ Records the beginning of the step in real time and
        prints the current step/number of cells.
    """
    # records when the step begins, used for measuring efficiency
    simulation.step_start = time.perf_counter()    # time.perf_counter() is more accurate than time.time()

    # prints the current step number and the number of cells
    print("Step: " + str(simulation.current_step))
    print("Number of cells: " + str(simulation.number_cells))


@cuda.jit(device=True)
def magnitude(vector_1, vector_2):
    """ A just-in-time compiled cuda kernel device function
        for getting the distance between two vectors.
    """
    # loop over the axes add the squared difference
    total = 0
    for i in range(0, 3):
        total += (vector_1[i] - vector_2[i]) ** 2

    # return the sqrt of the total
    return total ** 0.5


def normal_vector(vector):
    """ Returns the normalized vector, sadly this does not
        exist in NumPy.
    """
    # get the magnitude of the vector
    mag = np.linalg.norm(vector)

    # if magnitude is 0 return zero vector, otherwise divide by the magnitude
    if mag == 0:
        return np.zeros(3)
    else:
        return vector / mag


def random_vector(simulation):
    """ Computes a random vector on the unit sphere centered
        at the origin.
    """
    # random angle on the cell
    theta = r.random() * 2 * math.pi

    # 2D vector: [x, y, 0]
    if simulation.size[2] == 0:
        return np.array([math.cos(theta), math.sin(theta), 0])

    # 3D vector: [x, y, z]
    else:
        phi = r.random() * 2 * math.pi
        radius = math.cos(phi)
        return np.array([radius * math.cos(theta), radius * math.sin(theta), math.sin(phi)])


def record_time(function):
    """ A decorator used to time individual methods. If a method is called
        more than once, the time will be cumulative for the step.
    """
    @wraps(function)
    def wrap(simulation, *args, **kwargs):    # args and kwargs are for additional arguments
        # get the start/end time and call the method
        start = time.perf_counter()
        function(simulation, *args, **kwargs)
        end = time.perf_counter()

        # make sure key exists
        if function.__name__ not in simulation.method_times.keys():
            simulation.method_times[function.__name__] = 0

        # add the time to the running count for this step (if method is called more than once)
        simulation.method_times[function.__name__] += end - start

    return wrap
