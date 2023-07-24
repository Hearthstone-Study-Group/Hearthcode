import os
import re
import copy
import json
import torch
from tqdm import tqdm
from transformers import GPT2Tokenizer
from definition.GAME_TAG import GAME_TAG, OPERABLE_GAME_TAG
from definition.TAG_ZONE import TAG_ZONE
from definition.OPTION_TYPE import OPTION_TYPE


class TransitionLoader:
    tag_map = OPERABLE_GAME_TAG()
    zone = [
        TAG_ZONE.SECRET,
        TAG_ZONE.HAND,
        TAG_ZONE.PLAY
    ]
    scope = [
        GAME_TAG.ENTITY_ID,
        GAME_TAG.HEALTH,
        GAME_TAG.ATK,
        GAME_TAG.COST,
        GAME_TAG.ARMOR,
        GAME_TAG.ZONE,
        GAME_TAG.CONTROLLER,
        GAME_TAG.CLASS,
        GAME_TAG.CARDRACE,
        GAME_TAG.CARDTYPE,
        GAME_TAG.SECRET,
        GAME_TAG.ZONE_POSITION
    ]

    def __init__(self, folder_path, tokenizer, max_length=1024, difference=False):
        self.folder_path = folder_path
        self.tokenizer = tokenizer
        if self.tokenizer.pad_token is None:
            self.tokenizer.pad_token = self.tokenizer.eos_token
        self.max_length = max_length
        self.difference = difference

    def load_json_data(self, file_path):
        with open(file_path, "r") as f:
            return json.load(f)

    def collate_sequence_data(self, data):
        # Collate state, action, and option data and add the reward term
        sequence_data = []
        result = data["metadata"]["result"]
        decay_factor = 0.9  # Decaying factor for the reward
        for state, action, next_state, option, next_option in zip(data["sequence"]["state"][:-1], data["sequence"]["action"][:-1], 
                                                                  data["sequence"]["state"][1:], data["sequence"]["option"][:-1], data["sequence"]["option"][1:]):
            reward = result * decay_factor
            decay_factor *= decay_factor  # Applying the decay factor recursively
            if int(action["type"]) == OPTION_TYPE.END_TURN:
                continue
            collated_data = (state, action, copy.deepcopy(next_state), reward, option, next_option)
            sequence_data.append(collated_data)
        return sequence_data

    def preprocess_state(self, state, option, strip=False):
        operable = [int(item["entity"]) for item in option]
        state = [copy.deepcopy(entity) for entity in state if not (len(entity["card_name"]) == 0 or
                                                    len(entity["card_description"]) == 0 or
                                                    len(entity["card_id"]) == 0 or
                                                    entity["card_id"] is None or
                                                    (str(GAME_TAG.ZONE) in entity["tags"] and
                                                     int(entity["tags"][str(GAME_TAG.ZONE)]) not in self.zone))]
        for entity in state:
            entity.pop("card_id", None)
            entity.pop("card_name", None)
            # entity.pop("card_description", None)
            for tag in list(entity["tags"]):
                if int(tag) not in self.scope:
                    if strip:
                        entity["tags"].pop(tag, None)
                elif int(tag) == GAME_TAG.ENTITY_ID:
                    if int(entity["tags"][tag]) in operable:
                        entity["tags"][str(OPERABLE_GAME_TAG.TAG_READY)] = "1"
            
            entity["named_tags"] = {}
            for tag in list(entity["tags"]):
                entity["named_tags"][str(self.tag_map[int(tag)])] = entity["tags"][tag]
            entity.pop("tags", None)
            
        return state

    def preprocess_input(self, item):
        ret = json.dumps(item, separators=(',', ':'))
        for removal in [
            "\"", "{", "}", "[", "]", "\'"
                ]:
            ret = ret.replace(removal, "")

        keywords = [
            "entity",
            "type",
            "sub_options",
            "sub_option",
            "position",
            "targets",
            "target",
            "card_id",
            "card_name",
            "card_description",
            "named_tags",
            "tags"
        ]
        for keyword in keywords:
            ret = ret.replace(keyword + ":", "")
        ret = ret.replace(":", " ").replace(",", " ").replace("\\n", " ")
        ret = re.sub(r"<.*?>", "", ret)
        return ret.lower()

    def check_data(self, sequence_data):
        # Check the collated data for model input
        data = []
        for state, action, next_state, reward, option, next_option in sequence_data:
            stripped_state = self.preprocess_state(state, option, True)
            tokenized_state = self.tokenizer(self.preprocess_input(action) + self.preprocess_input(stripped_state),
                                             max_length=self.max_length)
            tokenized_action = self.tokenizer(self.preprocess_input(action),
                                              max_length=self.max_length)
            if self.difference:
                full_next_state = self.preprocess_state(next_state, next_option, False)
                full_state = self.preprocess_state(state, option, False)
                stripped_next_state = self.calculate_difference(full_state, full_next_state)
            else:
                stripped_next_state = self.preprocess_state(next_state, next_option, True)
            tokenized_next_state = self.tokenizer(self.preprocess_input(stripped_next_state),
                                                  max_length=self.max_length)
            item = [len(tokenized_state["input_ids"]),
                    len(tokenized_action["input_ids"]),
                    len(tokenized_next_state["input_ids"])]
            data.append(item)
        return data
    
    def calculate_difference(self, state, next_state):
        state_dict = {entity["tags"][str(GAME_TAG.ENTITY_ID)]: entity["tags"] for entity in state}
        next_state_dict = {entity["tags"][str(GAME_TAG.ENTITY_ID)]: entity["tags"] for entity in next_state}
        difference = []
        for entity_id in next_state_dict:
            entity_difference = {}
            if entity_id in state_dict:
                for key in next_state_dict[entity_id]:
                    if key in state_dict[entity_id] and state_dict[entity_id][key] == next_state_dict[entity_id][key]:
                        continue
                    else:
                        entity_difference[key] = next_state_dict[entity_id][key]
            else:
                entity_difference = next_state_dict[entity_id]
            if len(entity_difference) > 0:
                entity_difference[str(GAME_TAG.ENTITY_ID)] = entity_id
                difference.append(entity_difference)
        return difference

    def tokenize_data(self, sequence_data):
        # Tokenize the collated data for model input
        tokenized_data = []
        for state, action, next_state, reward, option, next_option in sequence_data:
            # print(json.dumps(state, separators=(',', ':')))
            # Perform tokenization for state, action, and option (you may need to adjust this based on your data structure)
            
            stripped_state = self.preprocess_state(state, option, True)
            tokenized_state = self.tokenizer(self.preprocess_input(action) + self.preprocess_input(stripped_state), return_tensors="pt",
                                             max_length=self.max_length, padding='max_length')
            tokenized_action = self.tokenizer(self.preprocess_input(action), return_tensors="pt",
                                              max_length=self.max_length, padding='max_length')
            if self.difference:
                full_next_state = self.preprocess_state(next_state, next_option, False)
                full_state = self.preprocess_state(state, option, False)
                stripped_next_state = self.calculate_difference(full_state, full_next_state)
            else:
                stripped_next_state = self.preprocess_state(next_state, next_option, True)
            tokenized_next_state = self.tokenizer(self.preprocess_input(stripped_next_state), return_tensors="pt",
                                              max_length=self.max_length, padding='max_length')

            # Combine the tokenized data and add the reward term
            combined_data = {
                "input_ids_state": tokenized_state.input_ids,
                "attention_mask_state": tokenized_state.attention_mask,
                "input_ids_action": tokenized_action.input_ids,
                "attention_mask_action": tokenized_action.attention_mask,
                "input_ids_next_state": tokenized_next_state.input_ids,
                "attention_mask_next_state": tokenized_next_state.attention_mask,
                "reward": reward
            }
            tokenized_data.append(combined_data)
        return tokenized_data

    def get_data_loader(self, batch_size=32):
        # Load JSON files, collate data, and tokenize for model input
        data = []
        for file_name in os.listdir(self.folder_path):
            file_path = os.path.join(self.folder_path, file_name)
            json_data = self.load_json_data(file_path)
            sequence_data = self.collate_sequence_data(json_data)
            tokenized_data = self.tokenize_data(sequence_data)
            data.extend(tokenized_data)

        # Convert tokenized data into PyTorch DataLoader
        input_ids_state = torch.stack([item["input_ids_state"] for item in data])
        print(input_ids_state.shape)
        attention_mask_state = torch.stack([item["attention_mask_state"] for item in data])
        input_ids_action = torch.stack([item["input_ids_action"] for item in data])
        attention_mask_action = torch.stack([item["attention_mask_action"] for item in data])
        input_ids_next_state = torch.stack([item["input_ids_next_state"] for item in data])
        attention_mask_next_state = torch.stack([item["attention_mask_next_state"] for item in data])
        rewards = torch.tensor([item["reward"] for item in data])

        data_loader = torch.utils.data.DataLoader(
            torch.utils.data.TensorDataset(input_ids_state, attention_mask_state,
                                           input_ids_action, attention_mask_action,
                                           input_ids_next_state, attention_mask_next_state,
                                           rewards),
            batch_size=batch_size,
            shuffle=True
        )
        return data_loader

    def check_data_loader(self):
        # Load JSON files, collate data, and tokenize for model input
        data = []
        for file_name in os.listdir(self.folder_path):
            file_path = os.path.join(self.folder_path, file_name)
            json_data = self.load_json_data(file_path)
            sequence_data = self.collate_sequence_data(json_data)
            data_property = self.check_data(sequence_data)
            data.extend(data_property)
        return data


if __name__ == "__main__":
    # Example usage:
    folder_path = "./storage/v0.1"
    tokenizer = GPT2Tokenizer.from_pretrained("gpt2")
    data_loader = TransitionLoader(folder_path, tokenizer)
    training_data_loader = data_loader.get_data_loader(batch_size=32)
    for batch in training_data_loader:
        # Access batched data here
        input_ids_state, attention_mask_state, input_ids_action, attention_mask_action, input_ids_option, attention_mask_option, rewards = batch
        # Your training loop or other processing here
        print(input_ids_state)
