package com.qianjiangsoft.graphcore.param.card;

import lombok.Data;

import java.util.List;

@Data
public class CreateCardParam {

    private String name;

    private String key;

    private List<CardOptionParam> optionList;
}