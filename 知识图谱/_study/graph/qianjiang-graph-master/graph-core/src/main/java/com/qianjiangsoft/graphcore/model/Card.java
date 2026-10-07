package com.qianjiangsoft.graphcore.model;

import lombok.Data;

import java.util.List;

@Data
public class Card {

    private Integer id;

    private String name;

    private String key;

    private List<CardOption> optionList;
}