from PIL import Image, ImageDraw
import cv2
import csv
from scipy.spatial import Voronoi, voronoi_plot_2d
import numpy as np

def draw_image(simulation):
    """ Turns the graph into an image at each timestep
    """
    # increases the image counter by 1 each time this is called
    simulation.image_counter += 1
    image_quality = simulation.quality
    image_size = (image_quality * 1500, image_quality * 1500)

    # draws the background of the image
    background = Image.new("RGB", image_size, color=(255, 255, 255))
    image = ImageDraw.Draw(background)

    # bounds of the simulation used for drawing lines
    bounds = np.array([[0, 0],[0, simulation.size[1]],[simulation.size[0],simulation.size[1]],[simulation.size[0], 0]])

    # loops over all of the cells and draws the nucleus and radius
    for i in range(len(simulation.cells)):
        x, y, z = image_quality * simulation.cells[i].location
        membrane = image_quality * simulation.cells[i].radius
        nucleus = membrane * 0.3
        center = image_quality * 250

        if simulation.cells[i].state == "Pluripotent":
            color = 'green'
        else:
            color = 'red'

        membrane_circle = (x - membrane + center, y - membrane + center, x + membrane + center, y + membrane + center)
        # nucleus_circle = (x - nucleus + center, y - nucleus + center, x + nucleus + center, y + nucleus + center)

        image.ellipse(membrane_circle, outline="black", fill=color)
        # image.ellipse(nucleus_circle, outline="black", fill=color)

    # loops over all of the bounds and draws lines to represent the grid
    for i in range(len(bounds)):
        center = image_quality * 250
        x0, y0 = image_quality * bounds[i]
        radius = image_quality * 5
        width = image_quality * 10

        if i < len(bounds) - 1:
            x1, y1 = image_quality * bounds[i + 1]
        else:
            x1, y1 = image_quality * bounds[0]

        corners = (x0 - radius + center, y0 - radius + center, x0 + radius + center, y0 + radius + center)
        lines = (x0 + center, y0 + center, x1 + center, y1 + center)

        color = 'black'
        image.ellipse(corners, outline=color, fill=color)
        image.line(lines, fill=color, width=width)

    # saves the image as a .png
    background.save(simulation.path + "network_image_" + str(int(simulation.time_counter)) + ".png", 'PNG')

def image_to_video(simulation):
    """ Creates a video out of all the png images at
        the end of the simulation
    """
    image_quality = simulation.quality
    image_size = (image_quality * 1500, image_quality * 1500)

    # creates a base video file to save to
    video_path = simulation.path + 'network_video.avi'
    out = cv2.VideoWriter(video_path, cv2.VideoWriter_fourcc("M","J","P","G"), 1.0, image_size)

    # loops over all images and writes them to the base video file
    for i in range(simulation.image_counter):
        path = simulation.path + 'network_image_' + str(i) + ".png"
        image = cv2.imread(path)
        out.write(image)

    # releases the file
    out.release()

def create_csv(simulation):
    """ Outputs a .csv file of important Cell
        instance variables from each cell
    """
    # opens .csv file
    new_file = open(simulation.path + "network_values_" + str(int(simulation.time_counter)) + ".csv", "w")
    csv_write = csv.writer(new_file)
    csv_write.writerow(['X_position', 'Y_position', 'Z_position', 'X_velocity', 'Y_velocity', 'Z_velocity', 'Motion',
                        'Mass', 'Radius', 'FGFR', 'ERK', 'GATA6', 'NANOG', 'State', 'Differentiation_counter',
                        'Division_counter', 'Death_counter'])

    # each row is a different cell
    for i in range(len(simulation.cells)):
        x_pos = round(simulation.cells[i].location[0], 1)
        y_pos = round(simulation.cells[i].location[1], 1)
        z_pos = round(simulation.cells[i].location[2], 1)
        x_vel = round(simulation.cells[i].velocity[0], 1)
        y_vel = round(simulation.cells[i].velocity[1], 1)
        z_vel = round(simulation.cells[i].velocity[2], 1)
        motion = simulation.cells[i].motion
        mass = simulation.cells[i].mass
        radius = simulation.cells[i].radius
        fgfr = simulation.cells[i].booleans[0]
        erk = simulation.cells[i].booleans[1]
        gata = simulation.cells[i].booleans[2]
        nanog = simulation.cells[i].booleans[3]
        state = simulation.cells[i].state
        diff = round(simulation.cells[i].diff_counter, 1)
        div = round(simulation.cells[i].div_counter, 1)
        death = round(simulation.cells[i].death_counter, 1)

        # writes the row for the cell
        csv_write.writerow([x_pos, y_pos, z_pos, x_vel, y_vel, z_vel, motion, mass, radius, fgfr, erk, gata, nanog,
                            state, diff, div, death])

def save_file(simulation):
    """ Saves the simulation txt files
        and image files
    """
    # saves the .csv file with all the key information for each cell
    create_csv(simulation)

    # draws the image of the simulation
    draw_image(simulation)

    # voronoi(simulation)

def voronoi(simulation, path):

    points = np.empty((0, 2), int)

    for i in range(len(simulation.cells)):
        points = np.append(points, [simulation.cells[i].location[:2]], axis=0)

    vor = Voronoi(points)
    fig = voronoi_plot_2d(vor, show_vertices=False, line_colors='green', line_width=2, line_alpha=0.6, point_size=5)

    fig.savefig(path + "network_voronoi_" + str(int(simulation.time_counter)), quality=100)