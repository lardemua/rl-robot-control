#!/usr/bin/env python3

import numpy as np
import os
import random

from gymnasium.envs.registration import register
from gymnasium.utils.ezpickle import EzPickle
from gymnasium_robotics.envs.robot_env import MujocoRobotEnv

from utils import euler_to_quaternion, point_distance, random_euler_angles, quaternion_to_transformation_matrix


class LarccEnv(MujocoRobotEnv, EzPickle):
    """Class for Larcc environment inspired by the Fetch environments."""

    def __init__(self, random_start=False, distance_threshold=0.02, kp=0.5, ko=0.25, **kwargs):
        # random or fixed starting joint positions
        self.random_start = random_start

        # distance threshold for successful episode
        self.distance_threshold = distance_threshold

        # store initial distance and weights for reward computation
        self.kp = kp
        self.ko = ko
        self.pos_rewards = []
        self.quat_rewards = []
        self.bonus_rewards = []

        # store relevant vlues for goal generation
        self.table_pos = np.array([0.1, 0.16, 0.38])
        self.table_size = np.array([1.2, 0.68, 0.76])

        self.joint_names = [
            "shoulder_pan_joint",
            "shoulder_lift_joint",
            "elbow_joint",
            "wrist_1_joint",
            "wrist_2_joint",
            "wrist_3_joint"
        ]

        self.initial_joint_values = [
            -0.004053417836324513,
            -1.7941252193846644,
            1.5798662344561976,
            -1.4848967355540772,
            -1.63149339357485,
            -0.07133704820741826
        ]

        MujocoRobotEnv.__init__(
            self,
            model_path=os.path.join(os.path.dirname(__file__), "models/env.xml"),
            initial_qpos={ k: v for k,v in zip(self.joint_names, self.initial_joint_values) },
            n_actions=6,
            n_substeps=20,
            width=1280,
            height=720,
            default_camera_config = {
                "distance": 2.3,
                "azimuth": 132.0,
                "elevation": -10.0,
                "lookat": np.array([0, 0, 1])
            },
            **kwargs
        )

        EzPickle.__init__(self, reward_type="dense", **kwargs)
    
    def get_eef(self):
        body_id = self._mujoco.mj_name2id(self.model, self._mujoco.mjtObj.mjOBJ_BODY, "eef")
        return np.concatenate((self.data.xpos[body_id], self.data.xquat[body_id]))

    def set_goal(self, goal):
        self.goal = goal

        body_id = self._mujoco.mj_name2id(
            self.model, self._mujoco.mjtObj.mjOBJ_BODY, "target0"
        )
        self.model.body_pos[body_id] = self.goal[:3]
        self.model.body_quat[body_id] = self.goal[3:]
        self._mujoco.mj_forward(self.model, self.data)
    
    def validate_initial_qpos(self, qpos):
        if len(qpos) != 7:
            return False
        if qpos[0] < self.table_pos[0] - self.table_size[0]/2+0.1 or qpos[0] > self.table_pos[0] + self.table_size[0]/2-0.1:
            return False
        if qpos[1] < self.table_pos[1] - self.table_size[1]/2 or qpos[1] > self.table_pos[1] + self.table_size[1]/2-0.1:
            return False
        if qpos[2] < self.table_pos[2] + self.table_size[2]/2 + 0.1 or qpos[2] > self.table_pos[2] + self.table_size[2]/2 + 0.6:
            return False
        
        w, x, y, z = qpos[3:]

        tf = quaternion_to_transformation_matrix([w, x, y, z])

        z_axis = np.dot(tf,np.array([0, 0, 1, 0]))

        # guarantee that the orientation is pointing forward and slightly downwards
        if z_axis[2] >= np.sin(-np.pi/6) or z_axis[1] >= -np.sin(np.pi/4):
            return True
        else:
            return False

    # GoalEnv methods
    # ----------------------------

    def compute_reward(self, achieved_goal, goal, info):
        # compute the position reward
        pos_error = point_distance(goal[:3], achieved_goal[:3])
        pos_reward = np.clip(1 - pos_error, -1, 1) # [-1, 1]
        self.pos_rewards.append(pos_reward)

        # compute the orientation reward
        quat_reward = max(np.dot(goal[3:], achieved_goal[3:]), np.dot(goal[3:], -achieved_goal[3:])) # [-1, 1]
        self.quat_rewards.append(quat_reward)

        # compute the bonus reward
        bonus_reward = 1 if pos_reward > 1 - self.distance_threshold and quat_reward > 0.98 else 0
        self.bonus_rewards.append(bonus_reward)

        # compute the final reward
        return self.kp * pos_reward + self.ko * quat_reward + (1-self.kp-self.ko) * bonus_reward

    # RobotEnv methods
    # ----------------------------

    def _set_action(self, action):
        action = (
            action.copy()
        )  # ensure that we don't change the action outside of this scope

        # scale the action to a maximum movement of 0.1 radians in each joint
        action[:2] *= 0.08
        action[2:] *= 0.12

        # update the joint positions
        current_joint_pos = np.array([])
        for i in range(6):
            current_joint_pos = np.append(current_joint_pos, self._utils.get_joint_qpos(self.model, self.data, self.joint_names[i])[0])

        new_joint_pos = np.array(current_joint_pos) + action

        new_joint_pos = np.clip(new_joint_pos, -np.pi, np.pi)

        # apply action to simulation.
        for i in range(6):
           self._utils.set_joint_qpos(self.model, self.data, self.joint_names[i], new_joint_pos[i])

    def _get_obs(self):
        # dt = self.n_substeps * self.model.opt.timestep

        # observation
        observation = np.array([])
        for i in range(6):
            observation = np.append(observation, self._utils.get_joint_qpos(self.model, self.data, self.joint_names[i])[0])

            #TODO consider adding joint velocities
            # observation = np.append(observation, self._utils.get_joint_qvel(self.model, self.data, self.joint_names[i])[0])

        # robot_qvel *= dt # to match mujoco velocity

        # achieved_goal
        achieved_goal = self.get_eef()

        # desired_goal
        if len(self.goal)>0:
            goal = self.goal.copy()
        else:
            goal = np.zeros(7)

        return {
            "observation": observation,
            "achieved_goal": achieved_goal,
            "desired_goal": goal
        }

    def _sample_goal(self):
        goal_pos = np.array([
            self.table_pos[0] + random.uniform(-self.table_size[0]/2+0.1, self.table_size[0]/2-0.1),
            self.table_pos[1] + random.uniform(-self.table_size[1]/2, self.table_size[1]/2-0.1),
            self.table_pos[2] + self.table_size[2]/2 + random.uniform(0.1, 0.6)
        ])

        while True:
            w, x, y, z = euler_to_quaternion(*random_euler_angles())

            tf = quaternion_to_transformation_matrix([w, x, y, z])

            z_axis = np.dot(tf,np.array([0, 0, 1, 0]))

            # guarantee that the orientation is pointing forward and slightly downwards
            if z_axis[2] < np.sin(-np.pi/6) and z_axis[1] < -np.sin(np.pi/4):
                goal_quat = [w, x, y, z]
                break

        return np.concatenate((goal_pos, goal_quat))

    def _is_success(self, achieved_goal, desired_goal):
        #d = point_distance(achieved_goal[:3], desired_goal[:3])
        #return (d < self.distance_threshold).astype(np.float32)
        return len(self.bonus_rewards)>0 and self.bonus_rewards[-1] == 1

    def _step_callback(self):
        # function to apply additional constraints on the simulation
        # some constraint
        #....#

        # update
        self._mujoco.mj_forward(self.model, self.data)    

    def _render_callback(self):
        # Visualize target.
        body_id = self._mujoco.mj_name2id(
            self.model, self._mujoco.mjtObj.mjOBJ_BODY, "target0"
        )
        self.model.body_pos[body_id] = self.goal[:3]
        self.model.body_quat[body_id] = self.goal[3:]
        self._mujoco.mj_forward(self.model, self.data)

    def _reset_sim(self):
        self.data.time = self.initial_time
        self.data.qpos[:] = np.copy(self.initial_qpos)
        self.data.qvel[:] = np.copy(self.initial_qvel)
        if self.model.na != 0:
           self.data.act[:] = None

        # generate random qpos until a valid one is found
        if self.random_start:
            while True:
                self.data.qpos[:6] = np.random.uniform(-np.pi, np.pi, 6)
                self._mujoco.mj_forward(self.model, self.data)
                if self.validate_initial_qpos(self.get_eef()):
                    robot_in_table = False
                    for joint_name in ["shoulder_link", "upper_arm_link", "forearm_link", "wrist_1_link", "wrist_2_link", "wrist_3_link"]:
                        joint_id = self._mujoco.mj_name2id(self.model, self._mujoco.mjtObj.mjOBJ_BODY, joint_name)
                        if self.data.xpos[joint_id][2] < self.table_size[2]:
                            robot_in_table = True
                    
                    if not robot_in_table:
                        break

        return True

    def _env_setup(self, initial_qpos):
        for name, value in initial_qpos.items():
            self._utils.set_joint_qpos(self.model, self.data, name, value)
        self._utils.reset_mocap_welds(self.model, self.data)
        self._mujoco.mj_forward(self.model, self.data)

        for _ in range(10):
            self._mujoco_step(None)

    # executing _mujoco_step breaks the robot so the method is skipped
    def _mujoco_step(self, action):
       pass

# register the custom environment
register(
    # unique identifier for the env `name-version`
    id='Larcc',
    # path to the class for creating the env
    # Note: entry_point also accept a class as input (and not only a string)
    entry_point=LarccEnv,
    # Max number of steps per episode, using a `TimeLimitWrapper`
    max_episode_steps=50,
)
