#########################################################
# Name:    Simulation                                   #
# Author:  Jack Toppen                                  #
# Date:    3/17/20                                      #
#########################################################
import numpy as np
import networkx as nx
import platform
import matplotlib.path as mpltPath
import random as r
import Parallel

"""
The simulation class.
"""


class Simulation:
    """ called once holds important information about the
        simulation
    """

    def __init__(self, name, path, end_time, time_step, pluri_div_thresh, diff_div_thresh, pluri_to_diff, size,
                 diff_surround_value, functions, parallel, max_fgf4, bounds, death_threshold, move_time_step,
                 move_max_time, spring_constant, friction, energy_kept, neighbor_distance):

        """ Initialization function for the simulation setup.
            name: the simulation name
            path: the path to save the simulation information to
            start_time: the start time for the simulation
            end_time: the end time for the simulation
            time_step: the time step to increment the simulation by
            pluri_div_thresh: threshold for pluripotent cells to divide
            diff_div_thresh:  threshold for differentiated cells to divide
            pluri_to_diff: threshold for pluripotent cells to differentiate
            size: the size of the grid (dimension, rows, columns)
            spring_max: represents the max distance for spring and other interactions
            diff_surround_value: the amount of differentiated cells needed to surround
                a pluripotent cell inducing its differentiation
            functions: the boolean functions as a string from Model_Setup
            itrs: the max amount of times optimize will run
            error: the max error allowed for optimize
            parallel: whether some aspects are run on the gpu
            max_fgf4: the limit a patch will hold for fgf4
            bounds: the bounds of the simulation
            spring_constant: strength of spring
        """
        self.name = name
        self.path = path
        self.end_time = end_time
        self.time_step = time_step
        self.pluri_div_thresh = pluri_div_thresh
        self.diff_div_thresh = diff_div_thresh
        self.pluri_to_diff = pluri_to_diff
        self.size = size
        self.diff_surround_value = diff_surround_value
        self.functions = functions
        self.parallel = parallel
        self.max_fgf4 = max_fgf4
        self.bounds = bounds
        self.death_threshold = death_threshold
        self.move_time_step = move_time_step
        self.move_max_time = move_max_time
        self.spring_constant = spring_constant
        self.friction = friction
        self.energy_kept = energy_kept
        self.neighbor_distance = neighbor_distance

        # counts how many times an image is created for making videos
        self.image_counter = 0

        # keeps a running count of the time
        self.time_counter = 0.0

        # array to hold all of the cell objects
        self.cells = np.array([], dtype=np.object)

        # array to hold all of the gradient objects
        self.gradients = np.array([], dtype=np.object)

        # graph representing all the cells and their connections to other cells
        self.network = nx.Graph()

        # holds the objects until they are added or removed from the simulation
        self.cells_to_remove = np.array([], dtype=np.object)
        self.cells_to_add = np.array([], dtype=np.object)

        # which file separator to use
        if platform.system() == "Windows":
            # windows
            self.sep = "\\"
        else:
            # linux/unix
            self.sep = "/"

        # bounds of the simulation used in cell division
        self.boundary = mpltPath.Path(self.bounds)


    def info(self):
        """ prints information about the simulation as it
            runs. May include more information later
        """
        print("Time: " + str(self.time_counter))
        print("Number of objects: " + str(len(self.cells)))

    def initialize_gradients(self):
        """ adds initial concentrations to each gradient grid
        """
        for i in range(len(self.gradients)):
            self.gradients[i].initialize_grid()

    def update_gradients(self):
        """ currently degrades the concentrations of molecules
            in the grids
        """
        for i in range(len(self.gradients)):
            self.gradients[i].update_grid()

    def update_cells(self):
        """ updates each cell by allowing them to divide
            and differentiate among other things
        """
        for i in range(len(self.cells)):
            self.cells[i].update_cell(self)

    def kill_cells(self):
        """ kills the cells that are alone for too long
        """
        for i in range(len(self.cells)):
            self.cells[i].kill_cell(self)

    def diff_surround_cells(self):
        """ increases the differentiation counter if enough
            differentiated cells surround a pluripotent cell
        """
        for i in range(len(self.cells)):
            self.cells[i].diff_surround(self)

    def add_gradient(self, grid):
        """ adds a gradient object to the simulation instance
        """
        self.gradients = np.append(self.gradients, grid)

    def add_cell(self, cell):
        """ Adds the specified object to the array
            and the graph
        """
        # adds it to the array
        self.cells = np.append(self.cells, cell)

        # adds it to the graph
        self.network.add_node(cell)

    def remove_cell(self, cell):
        """ Removes the specified object from the array
            and the graph
        """
        # removes it from the array
        self.cells = self.cells[self.cells != cell]

        # removes it from the graph
        self.network.remove_node(cell)

    def update_cell_queue(self):
        """ Updates the object add and remove queue
        """
        print("Adding " + str(len(self.cells_to_add)) + " objects...")
        print("Removing " + str(len(self.cells_to_remove)) + " objects...")

        # loops over all objects to remove
        for i in range(len(self.cells_to_remove)):
            self.remove_cell(self.cells_to_remove[i])

        # loops over all objects to add
        for i in range(len(self.cells_to_add)):
            self.add_cell(self.cells_to_add[i])

        # clear the arrays
        self.cells_to_remove = np.array([], dtype=np.object)
        self.cells_to_add = np.array([], dtype=np.object)

    def add_object_to_addition_queue(self, cell):
        """ Will add an object to the simulation object queue
            which will be added to the simulation at the end of
            the update phase.
        """
        # adds object to array
        self.cells_to_add = np.append(self.cells_to_add, cell)

    def add_object_to_removal_queue(self, cell):
        """ Will add an object to the simulation object queue
            which will be removed from the simulation at the end of
            the update phase.
        """
        # adds object to array
        self.cells_to_remove = np.append(self.cells_to_remove, cell)

    def check_neighbors(self):
        """ checks all of the distances between cells
            if it is less than a set value create a
            connection between two cells.
        """
        # clears the current graph to prevent existing edges remaining
        self.network.clear()

        # tries to run the parallel version of the model
        if self.parallel:
            Parallel.check_neighbors_gpu(self)
        else:
            # loops over all objects
            for i in range(len(self.cells)):

                # adds all of the cells to the simulation
                self.network.add_node(self.cells[i])

                # loops over all objects not check already
                for j in range(i + 1, len(self.cells)):

                    # get the distance between cells
                    dist_vec = self.cells[i].location - self.cells[j].location

                    # get the magnitude of the distance vector
                    dist = np.linalg.norm(dist_vec)

                    # if the cells are close enough, add an edge between them
                    if dist <= self.neighbor_distance:
                        self.network.add_edge(self.cells[i], self.cells[j])

    def handle_collisions(self):

        if self.parallel:
            Parallel.handle_collisions_gpu(self)
        else:
            time_counter = 0
            while time_counter <= self.move_max_time:
                time_counter += self.move_time_step

                edges = list(self.network.edges())

                for i in range(len(edges)):
                    obj1 = edges[i][0]
                    obj2 = edges[i][1]

                    displacement = obj1.location - obj2.location

                    if np.linalg.norm(
                            displacement) < obj1.nuclear_radius + obj1.cytoplasm_radius + obj2.nuclear_radius + obj2.cytoplasm_radius:
                        displacement_normal = displacement / np.linalg.norm(displacement)

                        obj1_displacement = (obj1.nuclear_radius + obj1.cytoplasm_radius) * displacement_normal
                        obj2_displacement = (obj2.nuclear_radius + obj1.cytoplasm_radius) * displacement_normal

                        real_displacement = (displacement - (obj1_displacement + obj2_displacement)) / 2

                        obj1.velocity[0] -= real_displacement[0] * (
                                    self.energy_kept * self.spring_constant / obj1.mass) ** 0.5
                        obj1.velocity[1] -= real_displacement[1] * (
                                    self.energy_kept * self.spring_constant / obj1.mass) ** 0.5

                        obj2.velocity[0] += real_displacement[0] * (
                                    self.energy_kept * self.spring_constant / obj2.mass) ** 0.5
                        obj2.velocity[1] += real_displacement[1] * (
                                    self.energy_kept * self.spring_constant / obj2.mass) ** 0.5

                for i in range(len(self.objects)):
                    velocity = self.objects[i].velocity

                    movement = velocity * self.move_time_step
                    self.objects[i].disp_vec += movement

                    new_velocity = np.array([0.0, 0.0])

                    new_velocity[0] = np.sign(velocity[0]) * max(
                        (velocity[0]) ** 2 - 2 * self.friction * abs(movement[0]), 0.0) ** 0.5
                    new_velocity[1] = np.sign(velocity[0]) * max(
                        (velocity[1]) ** 2 - 2 * self.friction * abs(movement[1]), 0.0) ** 0.5

                    self.objects[i].velocity = new_velocity


                self.update_constraints()

                self.check_neighbors()

    def update_constraints(self):
        for i in range(len(self.objects)):

            self.objects[i].location += self.objects[i].disp_vec

            if not 0 <= self.objects[i].location[0] <= 1000:
                self.objects[i].location[0] -= 2 * self.objects[i].disp_vec[0]

            if not 0 <= self.objects[i].location[1] <= 1000:
                self.objects[i].location[1] -= 2 * self.objects[i].disp_vec[1]

            # resets the movement vector to [0,0]
            self.objects[i].disp_vec = np.array([0.0, 0.0])

    def random_movement(self):
        """ has the objects that are in motion
            move in a random way
        """
        # loops over all objects
        for i in range(len(self.objects)):
            # finds the objects in motion
            if self.objects[i].motion:
                # new location of 10 times a random float from -1 to 1
                self.objects[i].disp_vec[0] += r.uniform(-1, 1) * 10
                self.objects[i].disp_vec[1] += r.uniform(-1, 1) * 10

        self.update_constraints()