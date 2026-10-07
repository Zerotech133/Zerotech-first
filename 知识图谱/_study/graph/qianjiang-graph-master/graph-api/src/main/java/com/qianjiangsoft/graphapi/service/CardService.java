package com.qianjiangsoft.graphapi.service;

import com.qianjiangsoft.graphapi.dto.CardListDTO;
import com.qianjiangsoft.graphapi.dto.CreateCardDTO;
import com.qianjiangsoft.graphcore.param.card.CreateCardParam;

import java.util.List;

/**
 * @author marshallwu
 * @date 2022/11/29 14:26
 */
public interface CardService {

    CreateCardDTO createCard(CreateCardParam param);

    List<CardListDTO> selectCardList();

    void deleteAll();

}
