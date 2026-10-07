package com.qianjiangsoft.graphapi.dto;

import lombok.Data;

import java.util.List;

@Data
public class CardListDTO {

    private Integer id;

    private String name;

    private String key;

    private List<CardOptionDTO> optionList;
}