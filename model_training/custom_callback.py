#!/usr/bin/env python3

import numpy as np
import os

from stable_baselines3.common.evaluation import evaluate_policy
from stable_baselines3.common.vec_env import sync_envs_normalization

from stable_baselines3.common.callbacks import EvalCallback, StopTrainingOnNoModelImprovement


class CustomCallback(EvalCallback):
    def __init__(self, eval_env, best_model_save_path):
        # Stop training if there is no improvement after more than 10 evaluations
        stop_train_callback = StopTrainingOnNoModelImprovement(max_no_improvement_evals=20, min_evals=60, verbose=1)
        super().__init__(eval_env, eval_freq=500*50, n_eval_episodes=100, callback_after_eval=stop_train_callback, verbose=1, best_model_save_path=best_model_save_path, deterministic=True)
        self.evaluations_results_pos = []
        self.evaluations_results_quat = []
        self.evaluations_results_bonus = []
    
    def _on_step(self) -> bool:
        continue_training = True

        if self.eval_freq > 0 and self.n_calls % self.eval_freq == 0:
            # Sync training and eval env if there is VecNormalize
            if self.model.get_vec_normalize_env() is not None:
                try:
                    sync_envs_normalization(self.training_env, self.eval_env)
                except AttributeError as e:
                    raise AssertionError(
                        "Training and eval env are not wrapped the same way, "
                        "see https://stable-baselines3.readthedocs.io/en/master/guide/callbacks.html#evalcallback "
                        "and warning above."
                    ) from e

            # Reset success rate buffer
            self._is_success_buffer = []

            episode_rewards, episode_lengths = evaluate_policy(
                self.model,
                self.eval_env,
                n_eval_episodes=self.n_eval_episodes,
                render=self.render,
                deterministic=self.deterministic,
                return_episode_rewards=True,
                warn=self.warn,
                callback=self._log_success_callback,
            )

            episode_rewards_pos = self.eval_env.envs[0].env.pos_rewards
            episode_rewards_quat = self.eval_env.envs[0].env.quat_rewards
            episode_rewards_bonus = self.eval_env.envs[0].env.bonus_rewards

            if self.log_path is not None:
                assert isinstance(episode_rewards, list)
                assert isinstance(episode_lengths, list)
                self.evaluations_timesteps.append(self.num_timesteps)
                self.evaluations_results.append(episode_rewards)
                self.evaluations_length.append(episode_lengths)
                self.evaluations_results_pos.append(episode_rewards_pos)
                self.evaluations_results_quat.append(episode_rewards_quat)
                self.evaluations_results_bonus.append(episode_rewards_bonus)

                kwargs = {}
                # Save success log if present
                if len(self._is_success_buffer) > 0:
                    self.evaluations_successes.append(self._is_success_buffer)
                    kwargs = dict(successes=self.evaluations_successes)

                np.savez(
                    self.log_path,
                    timesteps=self.evaluations_timesteps,
                    results=self.evaluations_results,
                    ep_lengths=self.evaluations_length,
                    **kwargs,
                )

            mean_reward, std_reward = np.mean(episode_rewards), np.std(episode_rewards)
            mean_pos_reward = np.mean(episode_rewards_pos)*50
            mean_quat_reward = np.mean(episode_rewards_quat)*50
            mean_bonus_reward = np.mean(episode_rewards_bonus)*50
            mean_ep_length, std_ep_length = np.mean(episode_lengths), np.std(episode_lengths)
            self.last_mean_reward = float(mean_reward)

            self.eval_env.envs[0].env.pos_rewards.clear()
            self.eval_env.envs[0].env.quat_rewards.clear()
            self.eval_env.envs[0].env.bonus_rewards.clear()

            if self.verbose >= 1:
                print(f"Eval num_timesteps={self.num_timesteps}, " f"episode_reward={mean_reward:.2f} +/- {std_reward:.2f}")
                print(f"Episode length: {mean_ep_length:.2f} +/- {std_ep_length:.2f}")
            # Add to current Logger
            self.logger.record("eval/mean_reward", float(mean_reward))
            self.logger.record("eval/mean_pos_reward", float(mean_pos_reward))
            self.logger.record("eval/mean_quat_reward", float(mean_quat_reward))
            self.logger.record("eval/mean_bonus_reward", float(mean_bonus_reward))
            self.logger.record("eval/mean_ep_length", mean_ep_length)

            if len(self._is_success_buffer) > 0:
                success_rate = np.mean(self._is_success_buffer)
                if self.verbose >= 1:
                    print(f"Success rate: {100 * success_rate:.2f}%")
                self.logger.record("eval/success_rate", success_rate)

            # Dump log so the evaluation results are printed with the correct timestep
            self.logger.record("time/total_timesteps", self.num_timesteps, exclude="tensorboard")
            self.logger.dump(self.num_timesteps)

            if mean_reward > self.best_mean_reward:
                if self.verbose >= 1:
                    print("New best mean reward!")
                if self.best_model_save_path is not None:
                    self.model.save(os.path.join(self.best_model_save_path, "best_model"))
                self.best_mean_reward = float(mean_reward)
                # Trigger callback on new best model, if needed
                if self.callback_on_new_best is not None:
                    continue_training = self.callback_on_new_best.on_step()

            # Trigger callback after every evaluation, if needed
            if self.callback is not None:
                continue_training = continue_training and self._on_event()

        return continue_training
