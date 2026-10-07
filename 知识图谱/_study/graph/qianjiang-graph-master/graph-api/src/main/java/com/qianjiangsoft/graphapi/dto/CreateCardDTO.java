package com.qianjiangsoft.graphapi.dto;

import lombok.Data;

import java.util.List;

@Data
public class CreateCardDTO {

    private Integer id;

    private String name;

    private String key;

    private List<CardOptionDTO> valueList;
}